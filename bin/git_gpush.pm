# Copyright (C) 2017 The Qt Company Ltd.
# Copyright (C) 2019 Oswald Buddenhagen
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
use Symbol qw(gensym);
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

# This is for bigger amounts of text.
sub _wrap_narrow($)
{
    $Text::Wrap::columns = min($tty_width, 80) + 1;
    return wrap("", "", $_[0]);
}

sub nwout($)
{
    print _wrap_narrow($_[0]);
}

sub nwerr($)
{
    print STDERR _wrap_narrow($_[0]);
}

sub nwfail($)
{
    nwerr($_[0]);
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
    FWD_STDIN => 2,
    NUL_STDOUT => 0,
    USE_STDOUT => 4,
    FWD_STDOUT => 8,
    NUL_STDERR => 0,
    USE_STDERR => 16,
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
    } elsif ($flags & FWD_STDIN) {
        # open3() closes the handle input is redirected from, which is
        # most definitely not what we want to happen to stdin.
        no warnings 'once';  # The parser doesn't see the string reference.
        open INPUT, '<&STDIN' or wfail("Failed to dup() stdin: $!\n");
        $in = \'<&INPUT';
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
    if ($flags & USE_STDERR) {
        $err = \$process{stderr};
        $$err = gensym();
    } elsif ($flags & FWD_STDERR) {
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
    if ($$process{stderr}) {
        close($$process{stderr}) or wfail("Failed to close error read pipe of '$cmd': $!\n");
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

# Read all of a process' output channel as a single string.
sub get_process_output($$)
{
    my ($process, $which) = @_;

    local $/;
    my $fh = $$process{$which};
    return <$fh>;
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
our $gitcommondir;  # $GIT_COMMON_DIR

sub goto_gitdir()
{
    my $cdup = read_cmd_line(0, 'git', 'rev-parse', '--show-cdup');
    fail("fatal: This operation must be run in a work tree\n") if (!defined($cdup));
    chdir($cdup) unless ($cdup eq "");
    $gitdir = read_cmd_line(0, 'git', 'rev-parse', '--git-dir');
    $gitcommondir = read_cmd_line(0, 'git', 'rev-parse', '--no-flags', '--git-common-dir');
}

# `git config --list` output, plus contents of .git-gpush-aliases' [config]
our %gitconfig;  # { key => [ value, ... ] }
# Pre-processed url.*.insteadOf mappings.
my @url_rewrites;
my @url_rewrites_push;

sub _load_git_config()
{
    # Read all git configuration at once, as that's faster than repeated
    # git invocations, especially under Windows.
    my $cfg = open_cmd_pipe(0, 'git', 'config', '-l', '-z');
    while (read_fields($cfg, my $entry)) {
        $entry =~ /^([^\n]+)\n(.*)$/;
        my ($key, $value) = ($1, $2);
        push @{$gitconfig{$key}}, $value;

        if ($key =~ /^url\.(.*)\.insteadof$/) {
            push @url_rewrites, [ $value, $1 ];
        } elsif ($key =~ /^url\.(.*)\.pushinsteadof$/) {
            push @url_rewrites_push, [ $value, $1 ];
        }
    }
    close_process($cfg);

    # Sort backwards, so longest match is hit first.
    @url_rewrites = sort { $$b[0] cmp $$a[0] } @url_rewrites;
    @url_rewrites_push = sort { $$b[0] cmp $$a[0] } @url_rewrites_push;
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

sub _rewrite_git_url($$)
{
    my ($url, $push) = @_;

    foreach my $ent ($push ? @url_rewrites_push : @url_rewrites) {
        my ($pfx, $sub) = @$ent;
        return $url if ($url =~ s/^\Q$pfx\E/$sub/);
    }
    return $url;
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
    my $grefs = $remote_refs{$remote};
    if ($grefs) {
        $heads{$_} = 1 foreach (values %$grefs);
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

sub get_1st_commit($)
{
    my ($parents) = @_;

    return @$parents ? $$parents[0] : 'ROOT';
}

sub get_1st_parent($)
{
    my ($commit) = @_;

    my $parents = $$commit{parents};
    return @$parents ? $$parents[0] : 'ROOT';
}

sub get_1st_parent_tree($)
{
    my ($commit) = @_;

    # Upstream commits are not visited, so we get no tree id. However,
    # as this code aims at series which were not rebased, using the base
    # commit itself will work just as well for the series' first commit.
    my $parents = $$commit{parents};
    my $parent_id = @$parents ? $$parents[0] : 'ROOT';
    my $parent = $commit_by_id{$parent_id};
    return $parent ? $$parent{tree} : $parent_id;
}

sub get_more_parents($)
{
    my ($commit) = @_;

    my $parents = $$commit{parents};
    return @$parents > 1 ? "@$parents[1..$#$parents]" : "";
}

# Enumerate commits from the specified tip down to the merge base
# with upstream. Merges are handled in --first-parent mode.
sub get_commits_free($)
{
    my ($tip) = @_;

    my @commits;
    while (1) {
        my $commit = $commit_by_id{$tip};
        last if (!$commit);
        unshift @commits, $commit;
        $tip = get_1st_parent($commit);
    }
    return \@commits;
}

# Enumerate the specified number of commits from the specified tip.
# Merges are treated in --first-parent mode.
sub get_commits_count($$$)
{
    my ($tip, $count, $tip_raw) = @_;

    my @commits;
    for (1 .. $count) {
        my $commit = $commit_by_id{$tip};
        wfail("Range $tip_raw:$count extends beyond the local branch.\n")
            if (!$commit);
        unshift @commits, $commit;
        $tip = get_1st_parent($commit);
    }
    return \@commits;
}

# Enumerate commits from the specified tip down to the specified base.
# Merges are treated in --first-parent mode.
sub get_commits_base($$$$)
{
    my ($base, $tip, $base_raw, $tip_raw) = @_;

    # An empty base is understood to mean the merge base with upstream.
    # This avoids the need to figure out the actual commit, which is
    # particularly useful in the presence of multiple bases due to merges.
    return get_commits_free($tip) if (!length($base));

    my @commits;
    while ($tip ne $base) {
        my $commit = $commit_by_id{$tip};
        wfail("$base_raw is not an ancestor of $tip_raw within the local branch.\n")
            if (!$commit);
        unshift @commits, $commit;
        $tip = get_1st_parent($commit);
    }
    return \@commits;
}

########################
# gerrit query results #
########################

use constant {
    RVRTYPE_NONE => 0,
    RVRTYPE_REV => 1,
    RVRTYPE_CC => 1  # Because Gerrit won't tell.
};

our %gerrit_info_by_key;
our %gerrit_info_by_sha1;
our %gerrit_infos_by_id;

##################
# state handling #
##################

# This is built upon Change objects with these attributes:
# - key: Sequence number. This runs independently from Gerrit, so
#   we can enumerate Changes which were never pushed, and to make
#   it possible to re-associate local Changes with remote ones.
# - grp: Group (series) sequence number.
# - id: Gerrit Change-Id.
# - src: Local branch name, or "-" if Change is on a detached HEAD.
# - tgt: Target branch name.
# - topic: Gerrit topic. Persisted only as a cache.
# - pushed: SHA1 of the commit this Change was pushed as last time
#   from this repository.
# - base: SHA1 of commit on top of which the entire series which this
#   Change is part of was pushed.
# - orig: SHA1 of the _local_ commit 'pushed' was derived from.
# - nbase/ntgt/ntopic: Non-committed values of the respective
#   attributes, used by --group mode.
# - exclude: Flag indicating whether the Change is excluded from
#   push --all mode.
# - hide: Flag indicating whether the Change is excluded from all
#   pushes.

my $next_key = 10000;
# All known Gerrit Changes for the current repository.
our %change_by_key;  # { sequence-number => change-object }
# Same, indexed by Gerrit Change-Id. A Change can exist on multiple branches.
our %changes_by_id;  # { gerrit-id => [ change-object, ... ] }

our $next_group = 10000;

our $last_gc = 0;

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

sub _commit_state($$)
{
    my ($blob, $new) = @_;

    run_process(0, 'git', 'update-index', '--add', '--cacheinfo', "100644,$blob,state");
    my $tree = read_cmd_line(0, 'git', 'write-tree');
    my $sha1 = read_cmd_line(0, 'git', 'commit-tree', '-m', 'Saving state', $tree);
    if ($new) {
        run_process(0, 'git', 'update-ref', 'refs/gpush/state-new', $sha1);
        return;
    }
    run_process(0, 'git', 'update-ref', '-m', $state_updater,
                   '--create-reflog', 'refs/gpush/state', $sha1);
}

sub save_state(;$$)
{
    my ($dry, $new) = @_;

    print "Saving ".($new ? "new " : "")."state".($dry ? " [DRY]" : "")." ...\n" if ($debug);
    my (@lines, @updates);
    my @fkeys = ('key', 'grp', 'id', 'src', 'tgt', 'topic', 'base',
                 'ntgt', 'ntopic', 'nbase', 'exclude', 'hide');
    my @rkeys = ('pushed', 'orig');
    if ($new) {
        push @lines, "verify $new", "updater $state_updater";
        push @fkeys, @rkeys;
        @rkeys = ();
    }
    push @lines,
        "next_key $next_key",
        "next_group $next_group",
        "last_gc $last_gc",
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
                # Support empty values without making the file look funny.
                # We assume that no property ever contains a literal "".
                $val = '""' if (!length($val));
                push @lines, "$ky $val";
            }
        }
        push @lines, "";
    }
    foreach my $ginfo (values %gerrit_info_by_key) {
        my $fetched = $$ginfo{fetched};
        next if (!defined($fetched));
        my $_fetched = \%{$$ginfo{_fetched}};
        for my $ky (keys %$fetched) {
            my ($val, $oval) = ($$fetched{$ky}, $$_fetched{$ky});
            if (!defined($val)) {
                push @updates, "delete refs/gpush/g$$ginfo{key}_$ky\n"
                    if (defined($oval));
            } else {
                push @updates, "update refs/gpush/g$$ginfo{key}_$ky $val\n"
                    if (!defined($oval) || ($oval ne $val));
            }
            $$_fetched{$ky} = $val;
        }
    }
    update_refs($dry ? DRY_RUN : 0, \@updates);

    # We save the state file in a git ref as well, so the entire state
    # can be synced between hosts with git operations.
    if ($new || ("@lines" ne "@$state_lines")) {
        my $sts = open_process(USE_STDIN | SILENT_STDIN | USE_STDOUT | FWD_STDERR,
                               'git', 'hash-object', '-w', '--stdin');
        write_process($sts, map { "$_\n" } @lines);
        my $blob = read_process($sts);
        close_process($sts);
        with_local_git_index(\&_commit_state, $blob, $new) if (!$dry);
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

sub load_state_file(;$)
{
    my ($new) = @_;

    my $ref = $new ? "state-new" : "state";
    my $sts = open_process(SOFT_FAIL | USE_STDOUT | NUL_STDERR,
                           'git', 'cat-file', '-p', "refs/gpush/$ref:state");
    $state_lines = read_process_all($sts);
    close_process($sts);
    return if (!@$state_lines);

    my $state_verify;
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
            } elsif ($1 eq "next_group") {
                $next_group = int($2);
            } elsif ($1 eq "last_gc") {
                $last_gc = int($2);
            } elsif ($new && ($1 eq "verify")) {
                $state_verify = $2;
            } elsif ($new && ($1 eq "updater")) {
                $state_updater = $2;
            } else {
                fail("Bad state file: Unknown header keyword '$1' at line $line.\n");
            }
        } else {
            if (!$change) {
                $change = {};
                $$change{line} = $line;
                push @changes, $change;
            }
            $$change{$1} = ($2 eq '""') ? "" : $2;
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

    return $state_verify;
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
            $$change{$3} //= $1;  # Don't overwrite value from new file.
            $$change{'_'.$3} = $1;
        } elsif (m,^(.{40}) refs/gpush/g(\d+)_(\d+)$,) {
            my $ginfo = \%{$gerrit_info_by_key{$2}};
            $$ginfo{key} = $2;
            $$ginfo{fetched}{$3} = $1;
            $$ginfo{_fetched}{$3} = $1;
        }
    }
    close_process($info);
    update_refs(0, \@updates);
}

sub load_state($)
{
    my ($all) = @_;

    print "Loading state ...\n" if ($debug);
    load_state_file();
    load_refs($all ? "refs/gpush/" : "refs/gpush/i*", "refs/heads/", "refs/remotes/");
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

sub format_commit($;$)
{
    my ($commit, $max) = @_;
    return format_subject($$commit{changeid}, $$commit{subject}, $max);
}

sub format_commits($;$)
{
    my ($commits, $prefix) = @_;

    $prefix = "  " if (!defined($prefix));
    my $output = "";
    foreach my $commit (@$commits) {
        $output .= $prefix.format_commit($commit, -length($prefix))."\n";
    }
    return $output;
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

sub report_local_changes($$;$$)
{
    my ($reports, $changes, $prefix, $suffix) = @_;

    foreach my $change (@$changes) {
        my $commit = $$change{local};
        push @$reports, {
            type => "change",
            id => $$commit{changeid},
            subject => $$commit{subject},
            prefix => $prefix // "  ",
            suffix => $suffix,
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

# Note: The return value is reliable only for the first call.
sub visit_commits($$;$)
{
    my ($tips, $args, $cid_opt) = @_;

    my @revs = grep {
        if ($commit_by_id{$_}) {
            print "Already visited $_.\n" if ($debug);
            0;
        } else {
            1;
        }
    } @$tips;
    return if (!@revs);

    # The grep above excludes tips which match previous tips or their
    # ancestors. A tip that is a descendant of a previous tip still
    # needs to be visited, but the traversal needs to be cut off, so
    # pass all previous tips as exclusions to git.
    state %visited;
    my @excl = map { "^$_" } keys %visited;
    $visited{$_} = 1 foreach (@revs);
    push @revs, @excl;

    my $commits = visit_commits_raw(\@revs, $args, $cid_opt);
    foreach my $commit (@$commits) {
        init_commit($commit);
        # This excludes tips that were in fact ancestors of other tips,
        # thereby cutting down the noise in the exclusion list.
        delete $visited{$_} foreach (@{$$commit{parents}});
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
    # When traversing PatchSets from Gerrit, we need to exclude the heads from the
    # gerrit remote as well, as they may be actually ahead of our merge-base with
    # upstream.
    return visit_commits($tips, \@upstream_excludes, $cid_opt);
}

my $analyzed_local_branch = 0;
# SHA1 of the local branch's merge base with upstream.
our $local_base;
# SHA1 of the local branch's tip.
our $local_tip;
# Mapping of Change-Ids to commits on the local branch.
my %changeid2local;  # { change-id => SHA1 }

sub _source_map_prepare();
sub source_map_assign($$);
sub source_map_traverse();
sub _source_map_finish_initial();

sub analyze_local_branch($)
{
    my ($tip) = @_;

    $analyzed_local_branch = 1;

    # Get the revs ...
    print "Enumerating local Changes ...\n" if ($debug);
    my $raw_commits = visit_local_commits([ $tip ]);
    return 0 if (!@$raw_commits);

    # ... then sanity-check a bit ...
    my %seen;
    foreach my $commit (@$raw_commits) {
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

    # Iff we have a detached HEAD, we don't know the tip yet, because
    # we resolve only named branches. In other cases, this is a no-op.
    $local_tip = $$raw_commits[-1]{id};

    my $commits = get_commits_free($local_tip);

    $local_base = $$commits[0]{parents}[0];

    # This needs to happen early, for parse_local_rev(), which
    # _source_map_prepare() calls.
    $changeid2local{$$_{changeid}} = $$_{id} foreach (@$commits);

    # ... then map them to Change objects ...
    _source_map_prepare();
    while (1) {
        foreach my $commit (@$commits) {
            source_map_assign($commit, undef);
        }
        last if (!source_map_traverse());
    }
    _source_map_finish_initial();

    # ... and then add them to the set of local Changes.
    my $idx = 0;
    my $prev;
    foreach my $commit (@$commits) {
        my $change = $$commit{change};
        $$change{local} = $commit;
        $$change{index} = $idx++;
        $$change{parent} = $prev;
        $$prev{child} = $change if ($prev);
        $prev = $change;
    }

    return 1;
}

##### ... and also for upstream commits.

# The requirements are quite different than for local commits:
# - we need only the Change-Id as meta data
# - we can just discard Changes without Id
# - we don't want to pollute the %commit_by_id namespace, as doing that
#   would violate the assumption that upstream commits are not visited

use constant _GIT_UPSTREAM_LOG_ARGS => ('-z', '--pretty=%H%x00%B');

sub visit_upstream_commits($$)
{
    my ($tips, $bases) = @_;

    my $base;
    if (@$bases == 1) {
        $base = $$bases[0];
    } else {
        # If we have multiple bases, we need to find the oldest one.
        $base = read_cmd_line(0, 'git', 'merge-base', '--octopus', @$bases);
    }

    my %changeids;
    my $log = open_process(USE_STDIN | USE_STDOUT | FWD_STDERR,
                           'git', 'log', _GIT_UPSTREAM_LOG_ARGS, @$tips, "^$base");
    while (read_fields($log, my ($commit, $message))) {
        my @ids = ($message =~ /^Change-Id: (.+)$/mg);
        $changeids{$ids[-1]} = 1 if (@ids);
        # We don't print debug messages here, as this would completely
        # flood the log.
    }
    close_process($log);
    print "Found ".int(keys %changeids)." Changes.\n" if ($debug);
    return \%changeids;
}

#####################
# change resolution #
#####################

# Resolve a possibly abbreviated Change-Id referring to a commit in
# the local branch.
# NB, the most likely source of abbreviated Ids is the gpush output.
sub _sha1_for_partial_local_id($)
{
    my ($id) = @_;

    state %changeid2local_cache;
    return $changeid2local_cache{$id} if (exists($changeid2local_cache{$id}));

    my $sha1 = $changeid2local{$id};
    if (!defined($sha1)) {
        my @sha1s;
        my $rx = qr/^\Q$id\E/;
        foreach my $changeid (keys %changeid2local) {
            push @sha1s, $changeid2local{$changeid} if ($changeid =~ $rx);
        }
        if (@sha1s) {
            fail("$id is ambiguous.\n") if (@sha1s != 1);
            $sha1 = $sha1s[0];
        }
    }

    $changeid2local_cache{$id} = $sha1;
    return $sha1;
}

use constant {
    SPEC_BASE => 0,
    SPEC_TIP => 1,
    SPEC_PARENT => 2
};

sub _finalize_parse_local_rev($$$)
{
    my ($rev, $out, $scope) = @_;

    # There is no point in restricting the base here - subsequent
    # use will find out soon enough if it is not reachable.
    return $out if ($scope == SPEC_BASE);
    return $out if (!$analyzed_local_branch || $commit_by_id{$out});
    fail("$rev is outside the local branch.\n");
}

sub _parse_local_rev_sym($$)
{
    my ($rev, $scope) = @_;

    my $out;
    if ($rev eq 'HEAD') {
        fail("HEAD is not valid for base revspecs.\n") if ($scope == SPEC_BASE);
        $out = $local_tip;
    } elsif ($rev eq 'ROOT') {
        fail("ROOT is not valid for tip revspecs.\n") if ($scope == SPEC_TIP);
        $out = $rev;
    } elsif (($rev eq '@{u}') || ($rev eq '@{upstream}')) {
        fail("\@{upstream} is not valid for tip revspecs.\n") if ($scope == SPEC_TIP);
        $out = "";
    } else {
        return (undef, undef);
    }
    return (_finalize_parse_local_rev($rev, $out, $scope), 1);
}

sub _parse_local_rev_id_only($$)
{
    my ($rev, $scope) = @_;

    fail("$rev is not a valid revspec.\n") if ($rev !~ /^(\w+)(.*)$/);

    # This looks like a valid revspec, but git failed to parse it.
    # Try to parse it as a Change-Id instead.
    my ($id, $rest) = ($1, $2);
    my $sha1 = _sha1_for_partial_local_id($id);
    wfail("$rev does not refer to a Change on the local branch.\n") if (!$sha1);
    return $sha1 if (!$rest);

    my $out = read_cmd_line(SOFT_FAIL, 'git', 'rev-parse', '--verify', '-q', $sha1.$rest);
    fail("$rev is not a valid revspec.\n") if (!$out);
    return _finalize_parse_local_rev($rev, $out, $scope);
}

sub parse_local_rev_id($$)
{
    my ($rev, $scope) = @_;

    my ($sout, $done) = _parse_local_rev_sym($rev, $scope);
    return $sout if ($done);
    return _parse_local_rev_id_only($rev, $scope);
}

# Parse a revision specification referring to a commit in the local branch.
# This will return undef for @{u}, HEAD, and Change-Ids if the local branch
# was not visited yet; call parse_local_rev_id() after doing so.
sub parse_local_rev($$)
{
    my ($rev, $scope) = @_;

    $rev = "HEAD".$rev if ($rev =~ /^[~^]/);
    my ($sout, $done) = _parse_local_rev_sym($rev, $scope);
    return $sout if ($done);
    my $out = read_cmd_line(SOFT_FAIL, 'git', 'rev-parse', '--verify', '-q', $rev);
    return _finalize_parse_local_rev($rev, $out, $scope) if ($out);
    return undef if (!$analyzed_local_branch);  # Come back later!
    return _parse_local_rev_id_only($rev, $scope);
}

################
# smart series #
################

sub assign_series($)
{
    my ($changes) = @_;

    my $gid = $next_group++;
    $$_{grp} = $gid foreach (@$changes);
}

# Deduce a series from a single commit.
# Merges are treated in --first-parent mode.
sub do_determine_series($;$$$)
{
    my ($change, $extend, $capture, $forward_only) = @_;

    print "Deducing series from $$change{id}\n" if ($debug);
    my (@prospects, @changes);
    my $group_key;
    my $rchange = $change;
    while (1) {
        my $gid = $$change{grp};
        if (!defined($gid)) {
            if ($capture && @changes) {
                print "Capturing loose $$change{id}\n" if ($debug);
                unshift @changes, $change;
            } else {
                print "Prospectively capturing loose $$change{id}\n" if ($debug);
                unshift @prospects, $change;
            }
        } else {
            if (@changes) {
                # We already have a proto-series.
                # Check whether the new candidate is part of it.
                if ($gid != $group_key) {
                    # Miss; end of series.
                    print "Breaking off at foreign bound $$change{id}\n" if ($debug);
                    last;
                }
                # Hit; add the Change to the series.
                print "Adding bound $$change{id} and ".int(@prospects)." prospect(s)\n"
                    if ($debug);
            } elsif (!@prospects || $extend) {
                # The specified tip Change is bound, or we are extending
                # and the proto-series so far consists of only loose Changes.
                print "Adding bound $$change{id} and ".int(@prospects)." prospect(s) at tip\n"
                    if ($debug);
                # This Change determines the series.
                $group_key = $gid;
            } else {
                # Stop when encountering a bound Change after only loose ones
                # (unless extending).
                print "Breaking off at bound $$change{id} after only loose\n" if ($debug);
                last;
            }
            unshift @changes, $change, @prospects;
            @prospects = ();
        }
        $change = $$change{parent};
        last if (!$change);
    }
    # We also do reverse traversal, so one can re-order the series including
    # the last Change, but continue to use the same push command from history.
    # But we don't reverse-traverse if the specified Change is loose (and we're
    # not extending), based on the assumption that it was meant to be the tip
    # - otherwise, the semantics get really unintuitive.
    return (\@prospects, undef, $change, []) if (!defined($group_key));
    return (\@changes, $group_key, @prospects ? $prospects[-1] : $change, [])
        if ($forward_only);
    my @rprospects;
    while (1) {
        $rchange = $$rchange{child};
        last if (!defined($rchange));
        my $gid = $$rchange{grp};
        if (!defined($gid)) {
            # We don't automatically capture loose Changes on top of the series,
            # again to keep the semantics sane.
            print "Prospectively capturing loose $$rchange{id} (reverse)\n" if ($debug);
            push @rprospects, $rchange;
        } else {
            if ($gid != $group_key) {
                print "Breaking off at foreign bound $$rchange{id} (reverse)\n" if ($debug);
                last;
            }
            print "Adding bound $$rchange{id} and ".int(@rprospects)." prospect(s) (reverse)\n"
                if ($debug);
            push @changes, @rprospects, $rchange;
            @rprospects = ();
        }
    }
    return (\@changes, $group_key, undef, \@prospects);
}

###################
# commit creation #
###################

# Cache of diffs. Global, so it can be flushed from the outside.
our %commit2diff;

# Apply one commit's diff on top of another commit; return the new tree.
sub apply_diff($$$)
{
    my ($commit, $base_id, $flags) = @_;

    my $sha1 = $$commit{id};
    my $diff = $commit2diff{$sha1};
    if (!defined($diff)) {
        my $show = open_cmd_pipe(0, 'git', 'diff-tree', '--binary', '--root', "$sha1^!");
        $diff = get_process_output($show, 'stdout');
        close_process($show);
        $commit2diff{$sha1} = $diff;
    }

    # We don't know the tree when the base is an upstream commit.
    # This is just fine, as this will be the first commit in the
    # series by definition, so we need to load it anyway. The
    # subsequent apply will change it in every case, so there
    # is no chance to recycle it, either.
    state $curr_tree = "";
    my $base = $commit_by_id{$base_id};
    run_process(FWD_STDERR, 'git', 'read-tree', ($base_id eq 'ROOT') ? '--empty' : $base_id)
        if (!$base || ($curr_tree ne $$base{tree}));
    $curr_tree = "";

    my $proc = open_process(SOFT_FAIL | USE_STDIN | SILENT_STDIN | NUL_STDOUT | $flags,
                            'git', 'apply', '--cached', '-C1', '--whitespace=nowarn');
    write_process($proc, $diff);
    my $errors = get_process_output($proc, 'stderr')
        if ($flags & USE_STDERR);
    close_process($proc);
    return (undef, $errors) if ($?);
    $curr_tree = read_cmd_line(0, 'git', 'write-tree');
    return ($curr_tree, undef);
}

# Create a commit object from the specified metadata.
sub create_commit($$$$$)
{
    my ($parents, $tree, $commit_msg, $author, $committer) = @_;

    ($ENV{GIT_AUTHOR_NAME}, $ENV{GIT_AUTHOR_EMAIL}, $ENV{GIT_AUTHOR_DATE}) = @$author;
    ($ENV{GIT_COMMITTER_NAME}, $ENV{GIT_COMMITTER_EMAIL}, $ENV{GIT_COMMITTER_DATE}) = @$committer;
    my @pargs = map { ('-p', $_) } @$parents;
    my $proc = open_process(USE_STDIN | SILENT_STDIN | USE_STDOUT | FWD_STDERR,
                            'git', 'commit-tree', $tree, @pargs);
    write_process($proc, $commit_msg);
    my $sha1 = read_process($proc);
    close_process($proc);

    my $commit = {
        id => $sha1,
        parents => $parents,
        tree => $tree
    };
    init_commit($commit);
    return $commit;
}

###################
# branch tracking #
###################

# This tracks the local branch each Change lives on, following cherry-picks
# and purges as much as possible.

# Format a single branch name for display.
sub _format_branch($)
{
    my ($branch) = @_;

    return ($branch ne '-') ? "'$branch'" : '<detached HEAD>';
}

# Format the source branch names from an array of Changes,
# using Oxford English grammar.
sub _format_branches_raw($)
{
    my ($changes) = @_;

    my @branches = sort map { _format_branch($$_{src}) } @$changes;
    my $str = "$branches[0]";
    if (@branches > 1) {
        if (@branches > 2) {
            $str .= ", $branches[$_]" for (1 .. @branches - 2);
            $str .= ",";
        }
        $str .= " and $branches[-1]";
    }
    return $str;
}

# As above, but with quantifiers to signify that we mean all branches.
sub _format_branches(@)
{
    my ($changes) = @_;

    my $str = _format_branches_raw($changes);
    return $str if (@$changes == 1);
    return "both $str" if (@$changes == 2);
    return "all of $str";
}

# Format the message about the outcome of a Change assignment attempt.
sub _format_result($$$@)
{
    my ($commit, $new, $fmt, @args) = @_;

    my $lbr = $local_branch // "-";
    return sprintf("%s\n    %son %s $fmt.\n", format_commit($commit),
                   $new ? "newly " : "", _format_branch($lbr), @args);
}

use constant {
    _SRC_NOOP => 0,
    _SRC_MOVE => 1,
    _SRC_COPY => 2,
    _SRC_HIDE => 3
};

my @sm_options;
my $sm_option_new;
my %sm_option_by_id;
my %sm_wanted;
my %sm_present;
my $sm_printed = 0;
my $sm_changed = 0;
my $sm_failed = 0;

# Parse a single command line option relating to source branch tracking.
# $arg is the currently processed argument, while $args are the remaining
# arguments on the command line. If $rmt_ok is true, plus-prefixed remote
# specifications are accepted as well; note that these are not removed
# from the command line, unlike local ones.
sub parse_source_option($$\@)
{
    my ($arg, $rmt_ok, $args) = @_;

    my $action;
    if ($arg eq "--move") {
        $action = _SRC_MOVE;
    } elsif ($arg eq "--copy") {
        $action = _SRC_COPY;
    } elsif ($arg eq "--hide") {
        $action = _SRC_HIDE;
    } else {
        return undef;
    }
    fail("$arg needs an argument.\n")
        if (!@$args || ($$args[0] =~ /^-/));
    my $orig = shift @$args;
    my $tip = $orig;
    my ($base, $count);
    my $branch = $1 if ($tip =~ s,/(.*)$,,);
    my $rmt_id;
    if ($rmt_ok && $tip =~ /^\+(\w+)/) {
        $rmt_id = $1;
        unshift @$args, $tip;
    } else {
        $base = $1 if ($tip =~ s,^(.*)\.\.,,);
        $count = $1 if ($tip =~ s,:(.*)$,,);
        $tip = undef if ($tip eq "new");
        fail("Specifying a commit count and a range base are mutually exclusive.\n")
            if (defined($base) && defined($count));
        if (defined($base) || defined($count)) {
            wfail("Specifying a commit count or range base is incompatible with range 'new'.\n")
                if (!defined($tip));
        } else {
            wfail("Automatic ranges are not supported with $arg."
                    ." Use $tip:1 if you actually meant a single Change.\n")
                if (defined($tip));
        }
    }
    fail("$arg does not support specifying a source branch.\n")
        if (defined($branch) && ($action != _SRC_MOVE));
    push @sm_options, {
        action => $action,
        orig => $orig,
        rmt_id => $rmt_id,
        base => defined($base) ? length($base) ? $base : '@{u}' : undef,
        tip => defined($tip) ? length($tip) ? $tip : 'HEAD' : undef,
        count => $count,
        branch => $branch
    };
    return 1;
}

# Do final sanity checking on the source branch tracking related commands,
# expand the supplied ranges into series of commits, and create a reverse
# mapping of commits to command objects.
sub _source_map_prepare()
{
    my $br = $local_branch // "-";
    foreach my $option (@sm_options) {
        my $sbr = $$option{branch};
        wfail("Source and target branch are both '$br' in attempt to move Changes.\n")
            if (defined($sbr) && ($sbr eq $br));

        my $rmt_id = $$option{rmt_id};
        if (defined($rmt_id)) {
            $sm_option_by_id{$rmt_id} = $option;
            next;
        }

        my $raw_tip = $$option{tip};
        if (defined($raw_tip)) {
            my $commits;
            my $tip = parse_local_rev($raw_tip, SPEC_TIP);
            my $raw_base = $$option{base};
            if (defined($raw_base)) {
                my $base = parse_local_rev($raw_base, SPEC_BASE);
                $commits = get_commits_base($base, $tip, $raw_base, $raw_tip);
            } else {
                my $count = $$option{count};
                $commits = get_commits_count($tip, $count, $raw_tip);
            }
            foreach my $commit (@$commits) {
                my $sha1 = $$commit{id};
                my $old_option = $sm_option_by_id{$sha1};
                wfail("Range $$option{orig} intersects $$old_option{orig} (at $sha1).\n")
                    if ($old_option);
                $sm_option_by_id{$sha1} = $option;
            }
            $$option{commits} = $commits;
            next;
        }

        wfail("Only one of --move, --copy, and --hide may be specified with 'new'.\n")
            if (defined($sm_option_new));
        $sm_option_new = $option;
    }
}

# Schedule the removal a single Change object from the state database.
sub _obliterate_change($$)
{
    my ($change, $changes) = @_;

    print "Obliterating $$change{key} ($$change{id}) from $$change{src}.\n" if ($debug);
    @$changes = grep { $_ != $change } @$changes;  # From %changes_by_id
    $$change{garbage} = 1;  # Mark it for %change_by_key traversal
}

# Visit the requested non-current local branches with the purpose of
# determining which Change objects still correspond with actual commits.
sub source_map_traverse()
{
    # It would be tempting to always query all local branches in
    # advance, to save the extra git call. However, this would also
    # collect *really* old branches, and even though they are short,
    # calculating the exclusion takes hundreds of milliseconds,
    # which is slower than an extra git call even on Windows.
    # Additionally, we need to traverse down to the push base of the
    # previously pushed Changes (in case they were upstreamed already),
    # and this can also have a significant cost if the corresponding
    # local branch advanced a lot since a Change was pushed last time,
    # so it's better to do that selectively.

    return if (!%sm_wanted);

    # This loop is likely to run only once per call, so don't bother
    # coalescing the resulting visit_*() calls.
    foreach my $br (keys %sm_wanted) {
        print "Investigating other local branch $br ...\n" if ($debug);
        my %present;
        my @changes = grep { $$_{src} eq $br } values %change_by_key;
        my (@missing, $utips);
        my $tip = $local_refs{$br};
        if (defined($tip)) {
            visit_local_commits([ $tip ]);
            my $commits = get_commits_free($tip);
            print "Still present on the branch:\n".format_commits($commits) if ($debug);
            if (@$commits) {
                %present = map { $$_{changeid} => 1 } @$commits;

                # Prepare garbage collection.
                @missing = grep { !defined($present{$$_{id}}) } @changes;
                $utips = [ get_1st_parent($$commits[0]) ];
            } else {
                @missing = @changes;
                $utips = [ $tip ];
            }
        } else {
            print "Branch disappeared.\n" if ($debug);
            # The branch was deleted, so all its Changes are missing.
            @missing = @changes;
            # For the upstream check, we fall back to the branches the Changes
            # were targeting. This is worse than using the actual upstream, as
            # the target may be outdated and the Change actually went to another
            # branch. Also, it's potentially more work.
            my $urefs = $remote_refs{$upstream_remote};
            if ($urefs) {
                my %utiph = map { $_ => 1 } grep { $_ } map { $$urefs{$$_{tgt}} } @changes;
                $utips = [ keys %utiph ];
            }
        }
        $sm_present{$br} = \%present;

        # We may need to garbage-collect the branch.
        # Failure to do this would affect cherry-picks of Changes that were
        # upstreamed - neither do we want to need --copy for Changes that are
        # actually gone, nor do we want them to be detected as moves (and thus
        # inherit the now incorrect target branch).
        if (@missing) {
            # Record the push base of each Change - if a Change is actually
            # in our upstream, it will be so between its push base (which
            # cannot be younger than the branch's tip at that time) and the
            # branch's merge base with upstream.
            my %bases = map { $_ => 1 } grep { $_ } map { $$_{base} } @missing;
            if (%bases) {
                print "Visiting upstream of other local branch $br ...\n" if ($debug);
                my $urevs = visit_upstream_commits($utips, [ keys %bases ]);
                foreach my $change (@missing) {
                    my $changeid = $$change{id};
                    _obliterate_change($change, $changes_by_id{$changeid})
                        if (defined($$urevs{$changeid}));
                }
            }
        }
    }
    %sm_wanted = ();
    return 1;
}

# Determine which of the Change objects in $changes still refer to
# actual commits.
sub _find_candidate_sources($$$)
{
    my ($changes, $vanished, $persisting) = @_;

    return 0 if (!$changes);

    my $lbr = $local_branch // "-";
    my $retry = 0;
    foreach my $chg (@$changes) {
        # Hidden Changes are supposed to be skipped over, so it
        # would be backwards to use them as sources for moves.
        next if ($$chg{hide});

        my $obr = $$chg{src};
        # The current branch is obviously not a sensible source.
        next if ($obr eq $lbr);

        # Note that detached HEADs "vanish" after switching to an actual
        # branch, thereby freeing all Changes assigned to them, sans the
        # garbage-collected ones.
        my $present = $sm_present{$obr};
        if (!$present) {
            # Ensure that the branch is visited and garbage-collected.
            $sm_wanted{$obr} = 1;
            $retry = 1;
        } elsif (defined($$present{$$chg{id}})) {
            print "$$chg{id} persists on $obr.\n" if ($debug);
            push @$persisting, $chg;
        } else {
            print "$$chg{id} vanished from $obr.\n" if ($debug);
            $$chg{vanished} = 1;
            push @$vanished, $chg;
        }
    }
    return $retry;
}

sub source_map_assign($$)
{
    my ($commit, $reference) = @_;

    my $change = $$commit{change};
    return $change if ($change);

    my $lbr = $local_branch // "-";

    my $changeid = $$commit{changeid};
    my $changes = $changes_by_id{$changeid};
    my %change_by_branch;
    if ($changes) {
        foreach my $chg (@$changes) {
            my $br = $$chg{src};
            $change_by_branch{$br} = $chg;
        }
    }
    $change = $change_by_branch{$lbr};
    my $new = !$change;

    my $option = $sm_option_by_id{$reference // $$commit{id}} // $sm_option_new;
    my $new_only = $option && !defined($$option{tip});
    my $action = $option ? $$option{action} : _SRC_NOOP;
    if ($action == _SRC_COPY || $action == _SRC_HIDE) {
        # Note: We don't bother finding Changes which could be recycled -
        # gpull will clean up.
        # We don't complain about no-ops, as they don't hurt.
        my $label = ($action == _SRC_HIDE) ? "hidden" : "copied";
        if ($new || !$new_only) {
            if ($new) {
                $change = {};
                _init_change($change, $changeid);
            }
            $$change{hide} = ($action == _SRC_HIDE) ? 1 : undef;
            wout(_format_result($commit, $new, "marked as $label"))
                if (!$quiet);
            goto PRINTED;
        }
        print "Already have $$change{key} ($changeid) on $lbr; not $label.\n"
            if ($debug);
        goto FOUND;
    }
    if ($new || ($action == _SRC_MOVE && !$new_only)) {
        my $schange;
        my $sbr = ($action == _SRC_MOVE) ? $$option{branch} : undef;
        if (defined($sbr)) {
            $schange = $change_by_branch{$sbr};
            if (!$schange) {
                werr("Change $changeid does not exist on '$sbr'; cannot move.\n");
                goto FAIL;
            }
            if ($$schange{hide}) {
                werr("Change $changeid is hidden on '$sbr'; cannot move.\n");
                goto FAIL;
            }
        } else {
            my (@vanished, @persisting);
            if (_find_candidate_sources($changes, \@vanished, \@persisting)) {
                print "Need to come back for $changeid.\n" if ($debug);
                return undef;
            }
            if ($new) {
                if (@vanished) {
                    if (@vanished > 1) {
                        werr(_format_result(
                                $commit, 1, "was previously on %s.\n"
                                ."  Use --move with a source, or use --copy/--hide",
                                _format_branches(\@vanished)));
                        goto FAIL;
                    }
                    $change = pop @vanished;
                    # Note: this is a post-fact notice, so if the user failed to use
                    # --copy/--hide in advance, they'll need to fix it laboriously.
                    # This seems acceptable, as moving is the much more common case.
                    wout(_format_result(
                            $commit, 1, "was previously on %s. Inferring move",
                            _format_branch($$change{src})))
                        if (!$quiet);
                    goto PRINTED;
                }
                if (!@persisting) {
                    # This is the common case: an entirely new Change.
                    $change = {};
                    _init_change($change, $changeid);
                    goto CHANGED;
                }
                if ($action != _SRC_MOVE) {
                    werr(_format_result(
                            $commit, 1, "exists also on %s.\n"
                            ."  Prune unneeded copies, or use --move/--copy/--hide",
                            _format_branches_raw(\@persisting)));
                    goto FAIL;
                }
            } else { # implies $action == _SRC_MOVE
                if (!$$change{hide}) {
                    # Attempts to move over active Changes are most likely mistakes.
                    werr(_format_result(
                            $commit, 0, "is not hidden.\n"
                            ."  Pass a source to --move if you really mean it"));
                    goto FAIL;
                }
                if (@vanished) {
                    if (@vanished > 1) {
                        werr(_format_result(
                                $commit, 0, "was previously on %s.\n"
                                ."  Pass a source to --move to disambiguate",
                                _format_branches(\@vanished)));
                        goto FAIL;
                    }
                    $schange = pop @vanished;
                } elsif (!@persisting) {
                    werr(_format_result(
                            $commit, 0, "exists on no other branch. Use --copy to unhide it"));
                    goto FAIL;
                }
            }
            if (!$schange) {
                if (@persisting > 1) {
                    werr(_format_result(
                            $commit, $new, "exists also on %s.\n"
                            ."  Prune unneeded copies, pass a source to --move,"
                                ." or use --copy/--hide",
                            _format_branches_raw(\@persisting)));
                    goto FAIL;
                }
                $schange = pop @persisting;
            }
            $sbr = $$schange{src};
        }  # !defined($sbr)
        if ($change) {
            # If the slot on the current branch is busy, we need to free it first.
            # Note that this might be a hidden entry from a previous --move/--hide.
            _obliterate_change($change, $changes);
        }
        if (!$$schange{vanished}) {
            # If the Change still does or might exist on the source branch, we
            # need to create a hidden entry for it, so it does not appear to be
            # new next time around.
            my %chg = (src => $sbr, grp => $$schange{grp}, hide => 1);
            _init_change(\%chg, $changeid);
            print "... and hidden on $sbr.\n" if ($debug);
        }
        wout(_format_result($commit, $new, "moved from %s", _format_branch($sbr)))
            if (!$quiet);
        $change = $schange;
        goto PRINTED;
    }
    print "Already have $$change{key} ($changeid) on $lbr; leaving alone.\n"
        if ($debug);
    goto FOUND;

  PRINTED:
    $sm_printed = 1 if (!$quiet);
  CHANGED:
    $$change{src} = $lbr;
    $sm_changed = 1;
  FOUND:
    $$commit{change} = $change;
    return $change;

  FAIL:
    $sm_failed = 1;
    return undef;
}

sub source_map_finish()
{
    exit(1) if ($sm_failed);
    print "\n" if ($sm_printed);  # Delimit from remaining output.
    $sm_printed = 0;
}

sub _source_map_finish_initial()
{
    foreach my $option (@sm_options) {
        # Only --copy & --hide imply grouping.
        my $action = $$option{action};
        next if ($action != _SRC_COPY && $action != _SRC_HIDE);

        my $commits = $$option{commits};
        next if (!$commits);
        assign_series(changes_from_commits($commits));
        $sm_changed = 1;
    }

    source_map_finish();
    save_state() if ($sm_changed);
}

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
            $$change{ntgt} = undef
                if (($$change{ntgt} // "") eq $abr);
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

    my ($url, $push) = (git_config('remote.'.$rmt.'.pushurl'), 1);
    ($url, $push) = (git_config('remote.'.$rmt.'.url'), 0) if (!$url);
    fail("Remote '$rmt' does not exist.\n") if (!$url);
    $url = _rewrite_git_url($url, $push);
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
        my ($subject, $status) = ($$review{'subject'}, $$review{'status'});
        defined($subject) or fail("Huh?! $changeid has no subject?\n");
        defined($status) or fail("Huh?! $changeid has no status?\n");
        my ($branch, $topic) = ($$review{'branch'}, $$review{'topic'});
        defined($branch) or fail("Huh?! $changeid has no branch?\n");
        my $pss = $$review{'patchSets'};
        defined($pss) or fail("Huh?! $changeid has no PatchSets?\n");
        my (@revs, %rev_map);
        foreach my $cps (@{$pss}) {
            my ($number, $ts, $revision, $base, $ref) =
                    ($$cps{'number'}, $$cps{'createdOn'}, $$cps{'revision'},
                     $$cps{'gpush-base'}, $$cps{'ref'});
            defined($number) or fail("Huh?! PatchSet in $changeid has no number?\n");
            defined($ts) or fail("Huh?! PatchSet $number in $changeid has no timestamp?\n");
            defined($revision) or fail("Huh?! PatchSet $number in $changeid has no commit?\n");
            defined($ref) or fail("Huh?! PatchSet $number in $changeid has no ref?\n");
            my %rev = (
                id => $revision,
                ps => $number,
                ts => int($ts),
                base => $base,
                ref => $ref
            );
            $revs[$number] = \%rev;
            $rev_map{$revision} = \%rev;
            $gerrit_info_by_sha1{$revision} = $ginfo;
        }
        $$ginfo{key} = $key;
        $$ginfo{id} = $changeid;
        $$ginfo{subject} = $subject;
        $$ginfo{status} = $status;
        $$ginfo{branch} = $branch;
        $$ginfo{topic} = $topic;
        $$ginfo{revs} = [ grep { $_ } @revs ];  # Drop deleted ones.
        $$ginfo{rev_by_id} = \%rev_map;
        my $rvrs = $$review{'allReviewers'};
        if ($rvrs) {
            # Note: This does not differentiate between reviewers and CCs.
            # Reported upstream as https://crbug.com/gerrit/11709
            my %reviewers;
            foreach my $rvr (@$rvrs) {
                foreach my $ky ('name', 'email', 'username') {
                    my $val = $$rvr{$ky};
                    $reviewers{$val} = RVRTYPE_REV if (defined($val));
                }
            }
            $$ginfo{reviewers} = \%reviewers;
        }
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
