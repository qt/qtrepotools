# Copyright (C) 2017 The Qt Company Ltd.
# Contact: http://www.qt.io/licensing/
#
# You may use this file under the terms of the 3-clause BSD license.
# See the file LICENSE from this package for details.
#

package git_gpush;

use v5.14;
use strict;
use warnings;
no warnings qw(io);

use Carp;
$SIG{__WARN__} = \&Carp::cluck;
$SIG{__DIE__} = \&Carp::confess;

use List::Util qw(min max);
use File::Spec;
use File::Temp qw(mktemp);
use IPC::Open3 qw(open3);
use Term::ReadKey;
use Text::Wrap;
use JSON;

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

# Read all lines from the process' stdout.
sub read_process_all($)
{
    my ($process) = @_;

    my $fh = $$process{stdout};
    my @lines = <$fh>;
    chomp @lines;
    printf("Read %d lines.\n", int(@lines)) if ($debug);
    return \@lines;
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

our $_indexfile;
END { unlink($_indexfile) if ($_indexfile); }

sub with_local_git_index($@)
{
    my ($callback, @args) = @_;

    $_indexfile = mktemp(($ENV{TMPDIR} or "/tmp") . "/git-gpush.XXXXXX");
    local $ENV{GIT_INDEX_FILE} = $_indexfile;

    local ($SIG{HUP}, $SIG{INT}, $SIG{QUIT}, $SIG{TERM});
    $SIG{HUP} = $SIG{INT} = $SIG{QUIT} = $SIG{TERM} = sub { exit; };

    my $ret = $callback->(@args);

    unlink($_indexfile);
    $_indexfile = undef;

    return $ret;
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

########################
# gerrit query results #
########################

our %gerrit_info_by_key;
our %gerrit_infos_by_id;

##################
# state handling #
##################

# This is built upon Change objects with these attributes:
# - key: Sequence number. This runs independently from Gerrit, so
#   we can enumerate Changes which were never pushed, and to make
#   it possible to re-associate local Changes with remote ones.
# - id: Gerrit Change-Id.
# - src: Local branch name, or "-" if Change is on a detached HEAD.
# - tgt: Target branch name.
# - pushed: SHA1 of the commit this Change was pushed as last time
#   from this repository.

my $next_key = 10000;
# All known Gerrit Changes for the current repository.
our %change_by_key;  # { sequence-number => change-object }
# Same, indexed by Gerrit Change-Id. A Change can exist on multiple branches.
our %changes_by_id;  # { gerrit-id => [ change-object, ... ] }

my $state_lines;
my $state_updater = ($0 =~ s,^.*/,,r)." @ARGV";

# Perform a batch update of refs.
sub update_refs($$)
{
    my ($flags, $updates) = @_;

    if (!@$updates) {
        print "No refs to update.\n" if ($debug);
        return;
    }
    my $pipe = open_process(USE_STDIN | FWD_STDERR | $flags, "git", "update-ref", "--stdin");
    write_process($pipe, @$updates);
    close_process($pipe);
}

sub _commit_state($)
{
    my ($blob) = @_;

    run_process(0, 'git', 'update-index', '--add', '--cacheinfo', "100644,$blob,state");
    my $tree = read_cmd_line(0, 'git', 'write-tree');
    my $sha1 = read_cmd_line(0, 'git', 'commit-tree', '-m', 'Saving state', $tree);
    run_process(0, 'git', 'update-ref', '-m', $state_updater,
                   '--create-reflog', 'refs/gpush/state', $sha1);
}

sub save_state(;$)
{
    my ($dry) = @_;

    print "Saving state".($dry ? " [DRY]" : "")." ...\n" if ($debug);
    my (@lines, @updates);
    my @fkeys = ('key', 'id', 'src', 'tgt');
    my @rkeys = ('pushed');
    push @lines,
        "next_key $next_key",
        "";
    foreach my $key (sort keys %change_by_key) {
        my $change = $change_by_key{$key};
        my $garbage = $$change{garbage};
        foreach my $ky (@rkeys) {
            my ($val, $oval) = ($garbage ? undef : $$change{$ky}, $$change{'_'.$ky});
            if (!defined($val)) {
                push @updates, "delete refs/gpush/i${key}_$ky\n"
                    if (defined($oval));
            } else {
                push @updates, "update refs/gpush/i${key}_$ky $val\n"
                    if (!defined($oval) || ($oval ne $val));
            }
            $$change{'_'.$ky} = $val;
        }
        next if ($garbage);
        foreach my $ky (@fkeys) {
            my $val = $$change{$ky};
            if (defined($val)) {
                push @lines, "$ky $val";
            }
        }
        push @lines, "";
    }
    update_refs($dry ? DRY_RUN : 0, \@updates);

    # We save the state file in a git ref as well, so the entire state
    # can be synced between hosts with git operations.
    if ("@lines" ne "@$state_lines") {
        my $sts = open_process(USE_STDIN | SILENT_STDIN | USE_STDOUT | FWD_STDERR,
                               'git', 'hash-object', '-w', '--stdin');
        write_process($sts, map { "$_\n" } @lines);
        my $blob = read_process($sts);
        close_process($sts);
        with_local_git_index(\&_commit_state, $blob) if (!$dry);
        $state_lines = \@lines;
    } else {
        print "State file unmodified.\n" if ($debug);
    }
}

# Constructor for the Change object.
sub _init_change($$)
{
    my ($change, $changeid) = @_;

    print "Creating Change $next_key ($changeid).\n" if ($debug);
    $$change{key} = $next_key;
    $$change{id} = $changeid;
    push @{$changes_by_id{$changeid}}, $change;
    $change_by_key{$next_key} = $change;
    $next_key++;
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

sub load_state_file()
{
    my $sts = open_process(SOFT_FAIL | USE_STDOUT | NUL_STDERR,
                           'git', 'cat-file', '-p', 'refs/gpush/state:state');
    $state_lines = read_process_all($sts);
    close_process($sts);
    return if (!@$state_lines);

    my $line = 0;
    my $inhdr = 1;
    my $change;
    my @changes;
    foreach (@$state_lines) {
        $line++;
        if (!length($_)) {
            $inhdr = 0;
            $change = undef;
        } elsif (!/^(\w+) (.*)/) {
            fail("Bad state file: Malformed entry at line $line.\n");
        } elsif ($inhdr) {
            if ($1 eq "next_key") {
                $next_key = int($2);
            } else {
                fail("Bad state file: Unknown header keyword '$1' at line $line.\n");
            }
        } else {
            if (!$change) {
                $change = {};
                $$change{line} = $line;
                push @changes, $change;
            }
            $$change{$1} = $2;
        }
    }

    foreach my $change (@changes) {
        my $line = $$change{line};
        my ($key, $id) = ($$change{key}, $$change{id});
        fail("Bad state file: Change with no key at line $line.\n") if (!$key);
        fail("Bad state file: Change with no id at line $line.\n") if (!$id);
        fail("Bad state file: Change with duplicate id at line $line.\n")
            if (defined($change_by_key{$key}));
        $change_by_key{$key} = $change;
        push @{$changes_by_id{$id}}, $change;
    }
}

sub load_refs(@)
{
    my (@refs) = @_;

    my @updates;
    my $info = open_cmd_pipe(0, 'git', 'for-each-ref', '--format=%(objectname) %(refname)', @refs);
    while (read_process($info)) {
        if (m,^(.{40}) refs/heads/(.*)$,) {
            $local_refs{$2} = $1;
        } elsif (m,^(.{40}) refs/remotes/([^/]+)/(.*)$,) {
            $remote_refs{$2}{$3} = $1;
        } elsif (m,^(.{40}) refs/gpush/i(\d+)_(.*)$,) {
            my $change = $change_by_key{$2};
            if (!$change) {
                my $ref = substr($_, 41);
                werr("Warning: Unrecognized Change key in state ref $ref - dropping.\n");
                # It would cause trouble once the key is re-used.
                push @updates, "delete $ref\n";
                next;
            }
            $$change{$3} = $1;
            $$change{'_'.$3} = $1;
        }
    }
    close_process($info);
    update_refs(0, \@updates);
}

sub load_state()
{
    print "Loading state ...\n" if ($debug);
    load_state_file();
    load_refs("refs/gpush/i*", "refs/heads/", "refs/remotes/");
}

##########################
# commit metadata output #
##########################

# Don't let lists get arbitrarily wide, as this makes them hard to follow.
use constant _LIST_WIDTH => 120;
# The _minimal_ width assumed for annotations, even if absent. This
# ensures that annotations always "stick out" on the right, even if only
# short subjects have annotations.
use constant _ANNOTATION_WIDTH => 6;
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
             $$report{prefix} // "", $$report{suffix} // "", $$report{annotation} // "");
    push @a, length($a[2]) + length($a[3]) + max(length($a[4]), _ANNOTATION_WIDTH);
    for (@_) { $_ = shift @a; }
}

sub format_reports($)
{
    my ($reports) = @_;

    my $width = 0;
    foreach my $report (@$reports) {
        next if ($$report{type} ne "change");
        _unpack_report($report, my ($id, $subject, $pfx_, $sfx_, $ann_, $fixlen));
        my $w = length($subject);
        $w += _ID_WIDTH + 3 if (defined($id));
        $width = max($width, min($w, _SUBJECT_WIDTH) + $fixlen);
    }
    $width = min($width, $tty_width, _LIST_WIDTH);
    my $output = "";
    foreach my $report (@$reports) {
        my $type = $$report{type} // "";
        if ($type eq "flowed") {
            $output .= wrap("", "", $_)."\n" foreach (@{$$report{texts}});
        } elsif ($type eq "fixed") {
            $output .= join("", @{$$report{texts}});
        } elsif ($type eq "change") {
            _unpack_report($report, my ($id, $subject, $prefix, $suffix, $annot, $fixlen));
            my $w = $width - $fixlen;
            my $str = format_subject($id, $subject, min($w, _SUBJECT_WIDTH));
            my $spacing = length($annot) ? (" " x max($w - length($str), 0)) : "";
            $output .= $prefix.$str.$suffix.$spacing.$annot."\n";
        } else {
            die("Unknown report type '$type'.\n");
        }
    }
    return $output;
}

sub fail_formatted($)
{
    my ($reports) = @_;

    fail(format_reports($reports));
}

sub report_text($$@)
{
    my ($reports, $type, @texts) = @_;

    push @$reports, {
        type => $type,
        texts => \@texts
    };
}

sub report_flowed($@)
{
    my ($reports, @texts) = @_;

    push @$reports, {
        type => "flowed",
        texts => \@texts
    };
}

sub report_fixed($@)
{
    my ($reports, @texts) = @_;

    push @$reports, {
        type => "fixed",
        texts => \@texts
    };
}

sub set_change_error($$$)
{
    my ($change, $style, $error) = @_;

    ($$change{error_style}, $$change{error}) = ($style, $error);
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
            annotation => $$change{annotation}
        };
        my $error = $$change{error};
        if (defined($error)) {
            $error = (" " x (3 + _ID_WIDTH)).$error."\n"
                if (($$change{error_style} // 'oneline') eq 'oneline');
            report_fixed($reports, $error);
        }
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

###################
# branch tracking #
###################

# Update the target branches of local Changes according to the data
# from a Gerrit query.
# Each SHA1 is assigned to exactly one PatchSet, which in turn is
# assigned to exactly one Change. That way we can identify each Change
# by the last SHA1 we pushed it with, irrespective of the branch it is
# targeting. If the latter changes on Gerrit, we just follow it, as
# this is what the user usually wants.
sub _update_target_branches($)
{
    my ($ginfos) = @_;

    state %trackable_changes;
    if (!%trackable_changes) {
        # Changes on multiple local branches may have the same _pushed commit,
        # so make sure to collect only the Changes on the current branch.
        foreach my $change (values %change_by_key) {
            next if ($$change{garbage});
            next if (!$$change{local});
            my $pushed = $$change{pushed};
            $trackable_changes{$pushed} = $change if (defined($pushed));
        }
    }

    my @changed;
    my $need_save;
    foreach my $ginfo (@$ginfos) {
        foreach my $rev (@{$$ginfo{revs}}) {
            my $change = $trackable_changes{$$rev{id}};
            next if (!$change);
            my $pbr = $$change{tgt} // "";
            my $abr = $$ginfo{branch};
            next if ($pbr eq $abr);
            # Old gpush versions don't store the target branch. Make no noise about that.
            if ((length($pbr) && !$quiet) || $debug) {
                $$change{annotation} = "  [$pbr => $abr]";
                push @changed, $change;
            }
            $$change{tgt} = $abr;
            $need_save = 1;
        }
    }
    if (@changed) {
        my @reports;
        report_flowed(\@reports,
                "Notice: Adjusting ".int(@changed)." Change(s) to server-side re-targeting:");
        report_local_changes(\@reports, \@changed);
        print format_reports(\@reports)."\n";  # Delimit from remaining output.
        # The Changes may be printed again later.
        delete $$_{annotation} foreach (@changed);
    }
    save_state() if ($need_save);
}

#######################
# gerrit ssh handling #
#######################

# SSH arguments for connecting the Gerrit instance.
our @gerrit_ssh;
# Target repository name on the Gerrit instance.
our $gerrit_project;

# Extract Gerrit configuration from the previously determined Gerrit remote.
sub set_gerrit_config($)
{
    my ($rmt) = @_;

    my $url = git_config('remote.'.$rmt.'.url');
    fail("Remote '$rmt' does not exist.\n") if (!$url);
    if ($url =~ m,^ssh://([^/:]+)(?::(\d+))?/(.*?)(?:\.git)?/?$,) {
        push @gerrit_ssh, '-p', $2 if (defined($2));
        push @gerrit_ssh, $1;
        $gerrit_project = $3;
    } elsif ($url =~ m,^([^/:]+):([^/].*?)(?:\.git)?/?$,) {
        push @gerrit_ssh, $1;
        $gerrit_project = $2;
    } else {
        fail("Remote '$rmt' does not use a supported protocol.\n")
    }
}

sub query_gerrit($;$)
{
    my ($ids, $extra) = @_;

    my @ginfos;
    my $info = open_cmd_pipe(0, 'ssh', @gerrit_ssh, 'gerrit', 'query', '--format', 'JSON',
                                '--no-limit', '--patch-sets', $extra ? @$extra : (),
                                "project:$gerrit_project", '\\('.join(' OR ', @$ids).'\\)');
    while (read_process($info)) {
        my $review = decode_json($_);
        defined($review) or fail("Cannot decode JSON string '".chomp($_)."'\n");
        my ($key, $changeid) = ($$review{'number'}, $$review{'id'});
        next if (!defined($key) || !defined($changeid));
        my $ginfo = \%{$gerrit_info_by_key{$key}};
        push @{$gerrit_infos_by_id{$changeid}}, $ginfo;
        my $status = $$review{'status'};
        defined($status) or fail("Huh?! $changeid has no status?\n");
        my $branch = $$review{'branch'};
        defined($branch) or fail("Huh?! $changeid has no branch?\n");
        my $pss = $$review{'patchSets'};
        defined($pss) or fail("Huh?! $changeid has no PatchSets?\n");
        my (@revs, %rev_map);
        foreach my $cps (@{$pss}) {
            my ($number, $revision) = ($$cps{'number'}, $$cps{'revision'});
            defined($number) or fail("Huh?! PatchSet in $changeid has no number?\n");
            defined($revision) or fail("Huh?! PatchSet $number in $changeid has no commit?\n");
            my %rev = (
                id => $revision,
                ps => $number
            );
            $revs[$number] = \%rev;
            $rev_map{$revision} = \%rev;
        }
        $$ginfo{id} = $changeid;
        $$ginfo{status} = $status;
        $$ginfo{branch} = $branch;
        $$ginfo{revs} = [ grep { $_ } @revs ];  # Drop deleted ones.
        $$ginfo{rev_by_id} = \%rev_map;
        push @ginfos, $ginfo;
    }
    close_process($info);

    _update_target_branches(\@ginfos);
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
