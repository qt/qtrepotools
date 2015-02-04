@goto invoke_perl

#!/usr/bin/perl

# Copyright (C) 2015 The Qt Company Ltd.
# Contact: http://www.qt.io/licensing/
#
# You may use this file under the terms of the 3-clause BSD license.
# See the file LICENSE from this package for details.
#

# Use modules --------------------------------------------------------
use strict;
use File::Basename;

my $script_path = dirname($0);
$script_path =~ s=^/(.)/=\1:\\=;
$script_path =~ s=/=\\=g;

# Simplify program name, if it is a path.
my $orig_argv0 = $0; #save the old $0
$0 =~ s/.*(\\|\/)//;

my @targetProjects;
my @allPossibleTargetProjects;
my $compiler = "msvc"; # Default compiler
my $verbose = 0;
my $openMonitor = 0;
my $configuration = "";
my $lastTarget = "";
my $projectFile = "";
my $isSolution = 0;
my $passOnArguments = "";
my $lastKnownArgument = 0;
my $onlyShow = 0;
my $buildforks = 15;

sub usage {
    printf "Usage:\n";
    printf "$0 [project file <.pro, .sln/.vcproj, or .dsw/.dsp>] [projects in the .sln file] \n";
    printf "       [-j<n>] [[-c] configuration] [--monitor] [--help | -help]\n\n";

    printf "     By default, without any parameters, $0 will build the project or whole\n";
    printf "     solution in debug configuration. If no .sln or .vcproj file is found\n";
    printf "     in the current directory, the first .pro is generated, and its resulting\n";
    printf "     project file is used.\n\n";

    printf "     If a directory contains more than one project/solution file, you'll have\n";
    printf "     to specify the target. If a project/solution contains more than one\n";;
    printf "     configuration containing \"debug\", you'll have to specify the correct one.\n";
    printf "     (If the last 'project' on the commandline is either \"debug\" or \"release\"\n";
    printf "     it is assumed that you meant a configuration, so for these you don't need to\n";
    printf "     specify the '-c' option. For all others, you'll need the '-c' option)\n\n";

    printf "     Specifying a project within a solution file lets you build just a project\n";
    printf "     and its dependencies.\n\n";

    printf "     -j<n>            Sets the number of jobs for this build using the /MAXCPUS=<n>\n";
    printf "                      option for MSVC builds, and the -j<n> option for MinGW builds\n";
    printf "     --n              Show everthing that will be done, but don't actually do them\n";
    printf "     --monitor        Opens the build monitor, if not already open. (same as\n";
    printf "                      specifying /buildmonitor)\n";
    printf "     --help, -help    This help\n\n";

    printf "     Specifying any other option will simply pass the option on to the\n";
    printf "     buildconsole command, including any other arguments following an the last\n";
    printf "     known $0 option.\n\n";

    printf " Ex:\n";
    printf "     $0 release\n";
    printf "                  Builds the project / solution in release\n";
    printf "     $0 debug release\n";
    printf "                  Builds the \"debug\" project in a solution in release\n";
    printf "     $0 release debug\n";
    printf "                  Builds the \"release\" project in a solution in debug\n";
    printf "     $0 some_project.vcproj release\n";
    printf "                  Builds the \"some_project\" project in release\n";
    printf "     $0 some_project.vcproj -c profiled\n";
    printf "                  Builds the \"some_project\" project in profiled configuration\n";
    printf "     $0 some_big_project.sln test_app -c profiled\n";
    printf "                  Builds the \"test_app\" project (and its dependencies) in the\n";
    printf "                  \"some_big_project\" solution in profiled configuration\n";

    exit( 0 );
}

######################################################################
# Syntax:  findFiles(dir, match, descend)
# Params:  dir, string, directory to search for name
#          match, string, regular expression to match in dir
#          descend, integer, 0 = non-recursive search
#                            1 = recurse search into subdirectories
#
# Purpose: Finds files matching a regular expression.
# Returns: List of matching files.
#
# Examples:
#   findFiles("/usr","\.cpp$",1)  - finds .cpp files in /usr and below
#   findFiles("/tmp","^#",0)      - finds #* files in /tmp
######################################################################
sub findFiles {
    my ($dir,$match,$descend) = @_;
    my ($file,$p,@files);
    local(*D);
    $dir =~ s=\\=/=g;
    ($dir eq "") && ($dir = ".");
    if ( opendir(D,$dir) ) {
        if ( $dir eq "." ) {
            $dir = "";
        } else {
            ($dir =~ /\/$/) || ($dir .= "/");
        }
        foreach $file ( readdir(D) ) {
            next if ( $file  =~ /^\.\.?$/ );
            $p = $file;
            ($file =~ /$match/) && (push @files, $p);
            if ( $descend && -d $p && ! -l $p ) {
                push @files, &findFiles($p,$match,$descend);
            }
        }
        closedir(D);
    }
    return @files;
}

sub findProjectFile {
    printf "Searching for sln/dsw files......";
    my @filesFound = findFiles(".", "^.*\.(sln|dsw)\$", 0);

    if (scalar(@filesFound) == 0) {
        printf "[none]\n";
        printf "Searching for vcproj/dsp files...";
        @filesFound = findFiles(".", "^.*\.(vcproj|dsp)\$", 0);
    }

    if (scalar(@filesFound) == 0) {
        printf "[none]\n";
        printf "Searching for Makefiles files....";
        @filesFound = findFiles(".", "^Makefile\$", 0);
        foreach my $file (@filesFound) {
            my @configs = findPossibleConfigurations($file, "", @targetProjects);

            # If the Makefile is for msvc, clear it, as we
            # want to use MSVC projects for that compiler
            @filesFound = () if ($compiler eq "msvc");
        }
    }

    if (scalar(@filesFound) == 0) {
        printf "[none]\n";
        printf "Searching for .pro files.........";
        @filesFound = findFiles(".", "^.*\.pro\$", 0);
    }

    if (scalar(@filesFound) == 0) {
        printf "[error]\n";
        printf "Found no possible project file! You can't build in this directory!\n";
        exit(1);
    }

    my $found = join(", ", @filesFound);
    printf "[$found]\n";
    if (scalar(@filesFound) > 1) {
        printf "Too many possible project files! You have to specify the correct file!\n";
        exit(2);
    }
    return pop @filesFound;
}

sub findPossibleConfigurations {
    my ($file, $conf, @prjs) = @_;
    my @possibleConf;

    # Load contents of $file into $filecontents
    open(I, "< " . $file) || die "Could not open $file for reading";
    my @filecontents = <I>;
    close I;

    # MSVC.NET Solution file -------
    if ($file =~ /\.sln$/) {
        my @confs = grep(/[ \t]*ConfigName\.\d+[ \t]+=.*$conf.*/i, @filecontents);
        foreach (@confs) {
            my $txt = $_;
            $txt =~ s,^[^=]*=\s*(.*)\s*$,\1,;
            $txt =~ tr/\r//d;
            push @possibleConf, $txt;
        }

        # 2005 version -------
        my @confs = grep(/[ \t]*$conf\|\w+[ \t]+=.*$conf\|.*/i, @filecontents);
        foreach (@confs) {
            my $txt = $_;
            $txt =~ s,^[^=]*=\s*(.*)\s*$,\1,;
            $txt =~ tr/\r//d;
            push @possibleConf, $txt;
        }


    # MSVC.NET Project file --------
    } elsif ($file =~ /\.vcproj$/) {
        # Resplit the file in XML elements instead
        my $flatContents = join(" ", @filecontents);
        $flatContents =~ s/[\r\n]//g;
        $flatContents =~ s/\s\s+/ /g;
        $flatContents =~ s/> />\n/g;
        @filecontents = split(/\n/, $flatContents);

        my @confs = grep(/^<Configuration /i, @filecontents);
        foreach (@confs) {
            my $txt = $_;
            $txt =~ s,^.*Name[^=]*=[^"]*"([^"]*).*,\1,;
            $txt =~ tr/\r//d;
            push @possibleConf, $txt if ($txt =~ /$conf/i);
        }

    # MSVC Workspace file ----------
    } elsif ($file =~ /\.dsw$/) {
        # Get all potential target projects from Workspace
        my @possiblePrjs = grep(/^Project:.*/i, @filecontents);
        my @temp;
        foreach (@possiblePrjs) {
            my $txt = $_;
            $txt =~ s/^Project: "([^"]*)"=(.*) - Package Owner.*[\n\r]*$/\1@@@\2/;
            $txt =~ tr/\r//d;
            push @temp, $txt;
        }
        @possiblePrjs = @temp;

        # Populate all the possible targets, used later for the "Targets: (all)" simplification
        foreach (@possiblePrjs) {
            my $txt = $_;
            $txt =~ s/(.*)@@@.*/\1/;
            $txt =~ tr/\r//d;
            push @allPossibleTargetProjects, $txt;
        }

        if (scalar(@targetProjects) == 0) {
            # No projects were specified, so we'll go through and populate the list with all of them
            foreach (@possiblePrjs) {
                my $txt = $_;
                $txt =~ s/(.*)@@@.*/\1/;
                $txt =~ tr/\r//d;
                push @targetProjects, $txt;
            }
        }

        # Go through every project file specified as target project, to check if they have any
        # configuration which is ambiguous to the one specified. *puh*
        my @dupConfs; # All possible subProject configurations will be stored here
                      # Any dups will be removed later
        foreach (@targetProjects) {
            my $myTrgPrj = $_;
            @temp = grep(/$myTrgPrj@@@.*/i, @possiblePrjs);
            foreach (@temp) {
                my $temp_prj = $_;
                my $subFile = $temp_prj;
                $subFile =~ s/.*@@@(.*)/\1/;
                $subFile =~ tr/\r//d;
                push @dupConfs, findPossibleConfigurations($subFile, $conf, @targetProjects);
            }
        }

        # Removing dups
        my %seen = ();
        $seen{$_}++ foreach (@dupConfs);

        @possibleConf = keys %seen;


    # MSVC Project file ------------
    } elsif ($file =~ /\.dsp$/) {
        #Remove everthing from "# Begin Target"
        my $flatContents = join("@@@", @filecontents);
        $flatContents =~ s/[\r\n]//g;
        $flatContents =~ s/^(.*)# Begin Target.*$/\1/;
        @filecontents = split(/@@@/, $flatContents);

        my @confs = grep(/.*"\$\(CFG\)" == .*$conf.*/i, @filecontents);
        foreach (@confs) {
            my $txt = $_;
            $txt =~ s,.*"\$\(CFG\)" ==[^"]*".* - ([^"]*)"\s*$,\1,;
            $txt =~ tr/\r//d;
            push @possibleConf, $txt;
        }


    # Makefile ------------
    } elsif ($file =~ /^Makefile$/) {
        my @confs = grep(/^\S*: .*/, @filecontents);
        foreach (@confs) {
            my $txt = $_;
            if ($txt =~ /win32-g\+\+/) {
                $compiler = "mingw";
            } elsif ($txt =~ /win32-icc/) {
                $compiler = "icc";
            } elsif ($txt =~ /win32-msvc/) {
                $compiler = "msvc";
            }
        }
        #printf("Possible configs: '%s', Possible targets: '%s'\n", join("', '", @possibleConf), join("', '", @allPossibleTargetProjects));
    }
    return @possibleConf;
}

while ( @ARGV ) {
    my $arg = shift @ARGV;

    if ($lastKnownArgument) {
        $passOnArguments .= "$arg ";
    } elsif ( $arg =~ /^(-+|\/).*$/ ) {
        # Commandlineoptions
        if ( $arg eq "--help" or $arg eq "-help" ) {
            usage;
        } elsif ( $arg eq "--monitor" ) {
            $openMonitor = 1;
        } elsif ( $arg eq "--n" ) {
            $onlyShow = 1;
        } elsif ( $arg eq "--verbose" ) {
            $verbose = 1;
        } elsif ( $arg eq "-j" ) {
            $buildforks = shift @ARGV;
        } elsif ( $arg =~ /^-j/ ) {
            $buildforks = $arg;
            $buildforks =~ s/-j//;
        } elsif ( $arg eq "-c" ) {
            $configuration = shift @ARGV;
            $configuration = "debug" if (!defined $configuration);
        } else {
            $passOnArguments .= "$arg ";
            $lastKnownArgument = 1;
        }

    } elsif ( $arg =~ /^.*\.(sln|dsw)/ ) {
        $projectFile = $arg;
        $isSolution = 1;
    } elsif ( $arg =~ /^.*\.(vcproj|dsp)/ ) {
        $projectFile = $arg;
        $isSolution = 0;
    } elsif ( $arg =~ /^Makefile.*/ ) {
        $projectFile = $arg;
    } else {
        push @targetProjects, $arg;
        $lastTarget = $arg;
    }
}

if ($projectFile eq "") {
    $projectFile = findProjectFile;
    if ($projectFile =~ /^.*\.pro/) {
        printf "Running qmake on .pro file.......";
        if($compiler eq "msvc") {
            system("qmake $projectFile -tp vc");
        }
        else {
            system("qmake $projectFile");
        }
        printf "[done]\n";
        # Find the project file, after qmake'ing
        $projectFile = findProjectFile;
    }
}

if ($configuration eq "" && $compiler eq "msvc") {
    # Check if the last item in @targetProjects is either "debug" or "release", if so
    # use that, and remove it from the stack.
    if ($lastTarget eq "debug" || $lastTarget eq "release") {
        $configuration = "$lastTarget";
        pop @targetProjects;
    } else {
        $configuration = "debug";
    }
}

if ($compiler eq "msvc") {
    my @fullConfigurationNames = findPossibleConfigurations($projectFile, $configuration, @targetProjects);
    if (scalar @fullConfigurationNames == 0) {
        printf "* No possible configuration matching your specification ($configuration).\n";
        exit(4);
    } elsif (scalar @fullConfigurationNames > 1
             && scalar grep("^$configuration\$", @fullConfigurationNames) > 1) {
        my $allConfs = join(", ", @fullConfigurationNames);
        printf "* Too many possible configurations ($allConfs), so you have to specify one\n";
        exit(3);
    }
    $configuration = pop @fullConfigurationNames;
}
my $projects = join(" ", @targetProjects);

printf "---\n";
printf "Using IncrediBuild for $compiler building:\n";
printf "    Project: $projectFile\n";
if ($projects eq "" || $projects eq join(" ", @allPossibleTargetProjects)) {
    printf "    Targets: (%s)\n", ($compiler eq "msvc") ? "all" : "first";
} else {
    printf "    Targets: $projects\n";
}
printf "         As: $configuration\n";
printf "  Arguments: $passOnArguments\n";

# Check if Makefile
#   Detect which compiler ($compiler)
#   Use xgconsole

my $build_cmd;
if ($compiler eq "msvc") {
    # MSVC buildconsole command --------------------------------------------------------------------
    my $prjComma = join(",", @targetProjects);
    $build_cmd = "buildconsole ";
    $build_cmd .= "$projectFile ";
    $build_cmd .= "-PRJ=\"$prjComma\" " if ($projects ne "");
    $build_cmd .= "-CFG=\"$configuration\" ";
    $build_cmd .= "-MAXCPUS=$buildforks ";
    $build_cmd .= "-OPENMONITOR " if ($openMonitor);
    $build_cmd .= "-USEENV $passOnArguments";
} else {
    # Makefile xgconsole command -------------------------------------------------------------------
    my $xgeprofile = $script_path;
    $xgeprofile .= ($compiler eq "mingw") ? "\\mingw-xge-profile.xge" : "\\icc-xge-profile.xge";
    $build_cmd = "xgconsole ";
    $build_cmd .= "-OPENMONITOR " if ($openMonitor);
    $build_cmd .= "-PROFILE=\"$xgeprofile\" ";
    $build_cmd .= "-COMMAND=\"";
    $build_cmd .= ($compiler eq "mingw") ? "mingw32-make" : "make";
    $build_cmd .= " -j$buildforks";
    $build_cmd .= " $projects" if ($projects ne "");
    $build_cmd .= "\" $passOnArguments";
}
printf "  Build CMD: $build_cmd\n";
printf "---\n";

if (!$onlyShow) {
    system($build_cmd);
    exit $? >> 8;
}

__END__

:invoke_perl
@perl -x %~f0 %*
