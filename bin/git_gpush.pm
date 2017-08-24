# Copyright (C) 2017 The Qt Company Ltd.
# Contact: http://www.qt.io/licensing/
#
# You may use this file under the terms of the 3-clause BSD license.
# See the file LICENSE from this package for details.
#

package git_gpush;

use strict;
use warnings;
no warnings qw(io);

use Carp;
$SIG{__WARN__} = \&Carp::cluck;
$SIG{__DIE__} = \&Carp::confess;

use List::Util qw(min max);
use File::Spec;
use IPC::Open3 qw(open3);
use Term::ReadKey;
use Text::Wrap;

our @_imported;
BEGIN {
    no strict 'refs';
    @_imported = keys %{__PACKAGE__.'::'};
}

##################
# shared options #
##################

our $debug = 0;
our $verbose = 0;
our $quiet = 0;
our $dry_run = 0;

##################
# message output #
##################

$| = 1;  # OUTPUT_AUTOFLUSH; in case we're redirecting debug output.

my ($tty_width, undef, undef, undef) = (-t STDOUT) ? GetTerminalSize() : (80);

# This is for messages which even after wrapping will rarely/barely
# exceed a single line, so it does not matter that the output possibly
# violates the "no more than 80 columns for flowed text" guideline.
sub _wrap_wide($)
{
    $Text::Wrap::columns = $tty_width + 1;
    return wrap("", "", $_[0]);
}

sub wout($)
{
    print _wrap_wide($_[0]);
}

sub werr($)
{
    print STDERR _wrap_wide($_[0]);
}

sub wfail($)
{
    werr($_[0]);
    exit(1);
}

sub fail($)
{
    print STDERR $_[0];
    exit(1);
}

#######################
# subprocess handling #
#######################

use constant {
    NUL_STDIN => 0,
    USE_STDIN => 1,
    # FWD_STDIN is not needed
    NUL_STDOUT => 0,
    USE_STDOUT => 4,
    FWD_STDOUT => 8,
    NUL_STDERR => 0,
    # USE_STDERR is not needed
    FWD_STDERR => 32,
    FWD_OUTPUT => 40,
    SILENT_STDIN => 64,  # Suppress debug output for stdin
    SOFT_FAIL => 256,    # A non-zero exit from the process is not fatal
    DRY_RUN => 512       # Don't actually run the command if $dry_run is true
};

sub _format_cmd(@)
{
    return join(' ', map { /\s/ ? '"' . $_ . '"' : $_ } @_);
}

sub open_process($@)
{
    my ($flags, @cmd) = @_;
    my %process;

    $flags &= ~DRY_RUN if (!$dry_run);
    $process{flags} = $flags;
    if ($flags & DRY_RUN) {
        print "+ "._format_cmd(@cmd)." [DRY]\n" if ($debug);
        return \%process;
    }
    my $cmd = _format_cmd(@cmd);
    $process{cmd} = $cmd;
    my ($in, $out, $err);
    if ($flags & USE_STDIN) {
        $in = \$process{stdin};
    } else {
        $in = \'<&NUL';
    }
    if ($flags & USE_STDOUT) {
        $out = \$process{stdout};
    } elsif ($flags & FWD_STDOUT) {
        $out = \'>&STDOUT';
    } else {
        $out = \'>&NUL';
    }
    if ($flags & FWD_STDERR) {
        $err = \'>&STDERR';
    } else {
        $err = \'>&NUL';
    }
    print "+ $cmd\n" if ($debug);
    open(NUL, '>'.File::Spec->devnull()) or wfail("Failed to open bitbucket: $!\n");
    eval { $process{pid} = open3($$in, $$out, $$err, @cmd); };
    wfail("Failed to run \"$cmd[0]\": $!\n") if ($@);
    close(NUL);
    return \%process;
}

sub close_process($)
{
    my ($process) = @_;

    if ($$process{flags} & DRY_RUN) {
        $? = 0;
        return 0;
    }
    my $cmd = $$process{cmd};
    if ($$process{stdout}) {
        close($$process{stdout}) or wfail("Failed to close read pipe of '$cmd': $!\n");
    }
    waitpid($$process{pid}, 0) or wfail("Failed to wait for '$cmd': $!\n");
    if ($? & 128) {
        wfail("'$cmd' crashed with signal ".($? & 127).".\n") if ($? != 141); # allow SIGPIPE
        $? = 0;
    } elsif ($? && !($$process{flags} & SOFT_FAIL)) {
        exit($? >> 8);
    }
    return 0;
}

# Write any number of lines to the process' stdin.
# The input is expected to already contain trailing newlines.
# This function must be called exactly once iff USE_STDIN is used.
# Note that this will deadlock with USE_STDOUT if the process outputs
# too much before all input is written.
sub write_process($@)
{
    my ($process, @input) = @_;

    my $stdin = $$process{stdin};
    my $silent = ($$process{flags} & SILENT_STDIN);
    my $dry = ($$process{flags} & DRY_RUN);
    local $SIG{PIPE} = "IGNORE";
    foreach (@input) {
        print "> $_" if ($debug && !$silent);
        print $stdin $_ if (!$dry);
    }
    $dry or close($stdin) or wfail("Failed to close write pipe of '$$process{cmd}': $!\n");
}

# Read a line from the process' stdout.
sub read_process($)
{
    my ($process) = @_;

    my $fh = $$process{stdout};
    $_ = <$fh>;
    if (defined($_)) {
        chomp;
        print "- $_\n" if ($debug);
    }
    return $_;
}

# Read any number of null-terminated fields from the process' stdout.
sub read_fields($@)
{
    my $process = shift;
    my $fh = $$process{stdout};
    return 0 if (eof($fh));
    local $/ = "\0";
    for (@_) { chop($_ = <$fh>); }
    return 1;
}

# The equivalent of system().
sub run_process($@)
{
    my ($flags, @cmd) = @_;

    close_process(open_process($flags, @cmd));
}

# The equivalent of popen("r").
sub open_cmd_pipe($@)
{
    my ($flags, @cmd) = @_;

    return open_process(USE_STDOUT | FWD_STDERR | $flags, @cmd);
}

# Run the specified command and try to read exactly one line from its stdout.
sub read_cmd_line($@)
{
    my ($flags, @cmd) = @_;

    my $proc = open_cmd_pipe($flags, @cmd);
    read_process($proc);
    close_process($proc);
    return $_;
}

##############
# git basics #
##############

our $gitdir;  # $GIT_DIR

sub goto_gitdir()
{
    my $cdup = read_cmd_line(0, 'git', 'rev-parse', '--show-cdup');
    fail("fatal: This operation must be run in a work tree\n") if (!defined($cdup));
    chdir($cdup) unless ($cdup eq "");
    $gitdir = read_cmd_line(0, 'git', 'rev-parse', '--git-dir');
}

# `git config --list` output, plus contents of .git-gpush-aliases' [config]
our %gitconfig;  # { key => [ value, ... ] }

sub _load_git_config()
{
    # Read all git configuration at once, as that's faster than repeated
    # git invocations, especially under Windows.
    my $cfg = open_cmd_pipe(0, 'git', 'config', '-l', '-z');
    while (read_fields($cfg, my $entry)) {
        $entry =~ /^([^\n]+)\n(.*)$/;
        push @{$gitconfig{$1}}, $2;
    }
    close_process($cfg);
}

sub git_configs($)
{
    my ($key) = @_;
    my $ref = $gitconfig{$key};
    return defined($ref) ? @$ref : ();
}

sub git_config($;$)
{
    my ($key, $dflt) = @_;
    my @cfg = git_configs($key);
    return scalar(@cfg) ? $cfg[-1] : $dflt;
}

#################
# configuration #
#################

sub _file_contents($)
{
    my ($filename) = @_;

    my @contents = "";
    my $fh;
    if (-e $filename && open($fh, "< $filename")) {
        @contents = <$fh>;
        close $fh;
    }
    return @contents;
}

our %aliases;  # { alias => login }

sub load_config()
{
    # Read config from .git-gpush-aliases file
    my $in_aliases = 1;
    foreach my $line (_file_contents($::script_path."/.git-gpush-aliases")) {
        chomp $line;
        $line =~ s,(#|//).*$,,;  # Remove any comments
        if ($line =~ /^\[([^]]+)\]/) {
            if ($1 eq "aliases") {
                $in_aliases = 1;
            } elsif ($1 eq "config") {
                $in_aliases = 0;
            } else {
                fail("Unrecognized section '$1' in alias file.\n");
            }
        } elsif ($line =~ /^\s*([^ =]+)\s*=\s*(.*?)\s*$/) {  # Capture the value
            if ($in_aliases) {
                for my $alias (split(/,/, $1)) {
                    $aliases{$alias} = $2;
                }
            } else {
                push @{$gitconfig{"gpush.$1"}}, $2;
            }
        }
    }

    _load_git_config();

    foreach (keys %gitconfig) {
        if (/^gpush\.alias\.(.*)$/) {
            $aliases{$1} = git_config($_);
        }
    }
}

######################
# branches & remotes #
######################

# The name of the local branch we're working with (not necessarily the
# current branch). May be undef.
our $local_branch;
# The tips of the local and remote branches.
our %local_refs;  # { branch => sha1 }
our %remote_refs;  # { remote => { branch => sha1 } }

# The name of the local branch's upstream remote, or a configurable fallback.
our $upstream_remote;
# The name of the Gerrit remote, possibly identical to the upstream remote.
our $remote;

# The tips of the remote branches which are excluded from commit listings.
# The commits are already prepared for git-rev-list (prefixed with '^').
our @upstream_excludes;

sub update_excludes()
{
    my %heads;
    my $urefs = $remote_refs{$upstream_remote};
    if ($urefs) {
        $heads{$_} = 1 foreach (values %$urefs);
    }
    @upstream_excludes = map { "^$_" } keys %heads;
}

sub setup_remotes($)
{
    my ($source) = @_;

    my %remotes;
    for my $ky (keys %gitconfig) {
        if ($ky =~ /^remote\.(.*)\.url$/) {
            $remotes{$1} = 1;
        }
    }
    fail("No remotes configured.\n")
        if (!%remotes);
    if (defined($local_branch)) {
        $upstream_remote = git_config("branch.$local_branch.remote");
        fail("$source has invalid upstream remote '$upstream_remote'.\n")
            if (defined($upstream_remote) && !defined($remotes{$upstream_remote}));
    }
    if (!defined($upstream_remote)) {
        $upstream_remote = git_config('gpush.upstream');
        if (defined($upstream_remote)) {
            fail("Remote '$upstream_remote' configured in gpush.upstream is invalid.\n")
                if (!defined($remotes{$upstream_remote}));
        } else {
            $upstream_remote = 'origin';
            if (!defined($remotes{$upstream_remote})) {
                my @remotes_arr = keys %remotes;
                fail("$source has no upstream remote, and cannot guess one.\n")
                    if (@remotes_arr != 1);
                $upstream_remote = $remotes_arr[0];
            }
        }
        wout("Notice: $source has no upstream remote; defaulting to $upstream_remote.\n")
            if (!$quiet);
    }
    if (defined($remote)) {
        fail("Specified Gerrit remote '$remote' is invalid.\n")
            if (!defined($remotes{$remote}));
    } else {
        $remote = git_config('gpush.remote');
        # If a remote is configured, use exactly that one.
        if (defined($remote)) {
            fail("Remote '$remote' configured in gpush.remote is invalid.\n")
                if (!defined($remotes{$remote}));
        } else {
            # Otherwise try 'gerrit', and fall back to the upstream remote.
            $remote = 'gerrit';
            $remote = $upstream_remote if (!defined($remotes{$remote}));
        }
    }
    update_excludes();
}

###################
# commit metadata #
###################

our %commit_by_id;

sub init_commit($)
{
    my ($commit) = @_;

    my $id = $$commit{id};
    # Duplicating is bad, because it would discard members which are not
    # necessarily re-instantiated. Also, it's an indicator of inefficiency.
    die("Commit $id already instantiated")
        if ($commit_by_id{$id});
    $commit_by_id{$id} = $commit;
}

sub changes_from_commits($)
{
    my ($commits) = @_;

    return [ map { $$_{change} } @$commits ];
}

##################
# state handling #
##################

# This is built upon Change objects with these attributes:
# - id: Gerrit Change-Id.
# - src: Local branch name, or "-" if Change is on a detached HEAD.

# Known Gerrit Changes for the current repository, indexed by Change-Id.
# A Change can exist on multiple branches, so the values are arrays.
our %changes_by_id;  # { gerrit-id => [ change-object, ... ] }

# Constructor for the Change object.
sub _init_change($$)
{
    my ($change, $changeid) = @_;

    print "Creating Change $changeid.\n" if ($debug);
    $$change{id} = $changeid;
    push @{$changes_by_id{$changeid}}, $change;
}

use constant {
    CREATE => 1
};

# Get a Change object for a given Id on the _local_ branch. If no such
# object exists, create a new one if requested, otherwise return undef.
sub change_for_id($;$)
{
    my ($changeid, $create) = @_;

    my $br = $local_branch // "-";
    my $chgs = $changes_by_id{$changeid};
    if ($chgs) {
        foreach my $chg (@$chgs) {
            return $chg if ($$chg{src} eq $br);
        }
    }
    if ($create) {
        my %chg = (src => $br);
        _init_change(\%chg, $changeid);
        return \%chg;
    }
    return undef;
}

sub load_refs(@)
{
    my (@refs) = @_;

    my $info = open_cmd_pipe(0, 'git', 'for-each-ref', '--format=%(objectname) %(refname)', @refs);
    while (read_process($info)) {
        if (m,^(.{40}) refs/heads/(.*)$,) {
            $local_refs{$2} = $1;
        } elsif (m,^(.{40}) refs/remotes/([^/]+)/(.*)$,) {
            $remote_refs{$2}{$3} = $1;
        }
    }
    close_process($info);
}

sub load_state()
{
    print "Loading state ...\n" if ($debug);
    load_refs("refs/heads/", "refs/remotes/");
}

##########################
# commit metadata output #
##########################

# Elide over-long Change subjects, as they add nothing but noise.
use constant _SUBJECT_WIDTH => 70;
# Truncation width for Change-Ids and SHA1s; empirically determined to be
# "sufficiently unambiguous".
use constant _ID_WIDTH => 10;

sub format_id($)
{
    my ($id) = @_;

    return substr($id, 0, _ID_WIDTH);
}

sub format_subject($$;$)
{
    my ($id, $subject, $max) = @_;

    $max = _SUBJECT_WIDTH if (!defined($max));
    $max += $tty_width if ($max < 0);
    $max -= _ID_WIDTH + 3 if (defined($id));
    $max = max(25, min(_SUBJECT_WIDTH, $max)) - 5;
    # Right-elide if subject is longer than $max + ellipsis:
    $subject =~ s/^(.{$max}).{6,}$/$1\[...]/;
    return $subject if (!defined($id));
    return format_id($id)." ($subject)";
}

sub _unpack_report($@)
{
    my $report = shift @_;
    my @a = ($$report{id}, $$report{subject},
             $$report{prefix} // "");
    push @a, length($a[2]);
    for (@_) { $_ = shift @a; }
}

sub format_reports($)
{
    my ($reports) = @_;

    my $output = "";
    foreach my $report (@$reports) {
        my $type = $$report{type} // "";
        if ($type eq "flowed") {
            $output .= wrap("", "", $_)."\n" foreach (@{$$report{texts}});
        } elsif ($type eq "change") {
            _unpack_report($report, my ($id, $subject, $prefix, $fixlen));
            my $str = format_subject($id, $subject, -$fixlen);
            $output .= $prefix.$str."\n";
        } else {
            die("Unknown report type '$type'.\n");
        }
    }
    return $output;
}

sub report_flowed($@)
{
    my ($reports, @texts) = @_;

    push @$reports, {
        type => "flowed",
        texts => \@texts
    };
}

sub report_local_changes($$)
{
    my ($reports, $changes) = @_;

    foreach my $change (@$changes) {
        my $commit = $$change{local};
        push @$reports, {
            type => "change",
            id => $$commit{changeid},
            subject => $$commit{subject},
            prefix => "  ",
        };
    }
}

#############################
# commit metadata retrieval #
#############################

use constant _GIT_LOG_ARGS =>
        ('-z', '--pretty=%H%x00%P%x00%T%x00%B%x00%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd');

# Retrieve metadata for commits reachable from the specified tips.
sub visit_commits_raw($$;$)
{
    my ($tips, $args, $cid_opt) = @_;

    return if (!@$tips);

    my @commits;
    my $log = open_process(USE_STDIN | USE_STDOUT | FWD_STDERR,
                           'git', 'log', _GIT_LOG_ARGS, @$args, '--stdin');
    write_process($log, map { "$_\n" } @$tips);
    my @author = (undef, undef, undef);
    my @committer = (undef, undef, undef);
    while (read_fields($log, my ($id, $parents, $tree, $message), @author, @committer)) {
        # We truncate the subject anyway, so using just the first line is OK.
        $message =~ /^(.*)$/m;
        my $subject = $1;

        my @cids = ($message =~ /^Change-Id: (.+)$/mg);
        my $changeid;
        if (!@cids) {
            fail(format_subject($id, $subject, -18)." has no Change-Id.\n")
                if (!$cid_opt);
        } else {
            # Gerrit uses the last Change-Id if multiple are present.
            $changeid = $cids[-1];
        }

        print "-- $id: ".format_subject($changeid, $subject, -45)."\n" if ($debug);

        unshift @commits, {
            id => $id,
            parents => [ split(/ /, $parents) ],
            changeid => $changeid,
            subject => $subject,
            message => $message,
            tree => $tree,
            author => [ @author ],  # Force copy, as these are ...
            committer => [ @committer ]  # ... not loop-local.
        };
    }
    close_process($log);
    return \@commits;
}

sub visit_commits($$;$)
{
    my ($tips, $args, $cid_opt) = @_;

    my $commits = visit_commits_raw($tips, $args, $cid_opt);
    foreach my $commit (@$commits) {
        init_commit($commit);
    }
    return $commits;
}

sub visit_local_commits($;$)
{
    my ($tips, $cid_opt) = @_;

    # We exclude all upstream heads instead of only that of the current branch,
    # because Gerrit will ignore all known commits (reviewed or not).
    # This matters for cross-branch pushes and re-targeted Changes, where a commit
    # can have known ancestors which aren't on its upstream branch.
    return visit_commits($tips, \@upstream_excludes, $cid_opt);
}

sub analyze_local_branch($)
{
    my ($tip) = @_;

    # Get the revs ...
    print "Enumerating local Changes ...\n" if ($debug);
    my $commits = visit_local_commits([ $tip ]);

    # ... then sanity-check a bit ...
    my %seen;
    foreach my $commit (@$commits) {
        my $subject = $$commit{subject};
        fail("Commit on ".($local_branch // "<detached HEAD>")." was meant to be squashed:\n  "
                .format_subject($$commit{id}, $subject, -2)."\n")
            if ($subject =~ /^(squash|fixup)! /);
        my $changeid = $$commit{changeid};
        my $excommit = $seen{$changeid};
        fail("Duplicate Change-Id $changeid on ".($local_branch // "<detached HEAD>").":\n  "
                .format_subject($$excommit{id}, $$excommit{subject}, -2)."\n  "
                .format_subject($$commit{id}, $subject, -2)."\n")
            if (defined($excommit));
        $seen{$changeid} = $commit;
    }

    # ... and then add them to the set of local Changes.
    foreach my $commit (@$commits) {
        my $change = change_for_id($$commit{changeid}, CREATE);
        $$commit{change} = $change;
        $$change{local} = $commit;
    }

    return $commits;
}

#############################
# export all public symbols #
#############################

sub import()
{
    no strict 'refs';

    my %imported = map { $_ => 1 } @_imported;
    undef @_imported;
    while (my ($name, $symbol) = each %{__PACKAGE__.'::'}) {
        next if (defined($imported{$name}));
        next if ($name =~ /^(_.*|BEGIN|END|a|b|import)$/);
        # $symbol values referring to constants are resolved, but we want to alias the
        # inline function. Other values are typeglobs which can be aliased directly.
        *{caller.'::'.$name} = !length(ref($symbol)) ? *$symbol : \&{__PACKAGE__.'::'.$name};
    }
}

1;
