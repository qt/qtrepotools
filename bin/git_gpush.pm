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
    die(_wrap_wide($_[0]));
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
    die("fatal: This operation must be run in a work tree\n") if (!defined($cdup));
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
                die("Unrecognized section '$1' in alias file.\n");
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
