#!/bin/bash
# Copyright (C) 2012 Nokia Corporation and/or its subsidiary(-ies).
# Contact: http://www.qt-project.org/
#
# You may use this file under the terms of the 3-clause BSD license.
# See the file LICENSE from this package for details.
#
#
#
# Script for archiving qt5 repositories
#
# Usage:
# ./mksrc.sh -u <file url to local git clone> -v <version>
#  - Currently supporting only local clones, not direct git:// url's
# After running the script, one will get qt-everywhere-opensource-src-<version>.tar.gz
# and qt-everywhere-opensource-src-<version>.zip
#

CUR_DIR=$PWD
REPO_DIR=$CUR_DIR
QTVER=0.0.0
QTSHORTVER=0.0
QTGITTAG=.sha1s
PACK_TIME=`date '+%Y-%m-%d'`
DOCS=generate
MULTIPACK=no


function tool_failure ()
{
  echo
  echo "*******************************************************"
  echo "* *************************************************** *"
  echo "* *                   NOTICE!!!                     * *"
  echo "* *                 SKIPPING DOCS                   * *"
  echo "* * Make sure you have correct path to working      * *"
  echo "* * qmake in default_src.config OR add it directly  * *"
  echo "* * into your PATH                                  * *"
  echo "* *************************************************** *"
  echo "*******************************************************"
  DOCS=skip
}

function usage()
{
  echo "Usage:"
  echo "./mksrc.sh -u <file_url_to_git_repo> -v <version> [-m]"
  echo "where -u is path to git repo and -v is version"
  echo "with -m one is able to tar each sub module separately"
}

function cleanup()
{
  echo "Cleaning all tmp artifacts"
  rm -f _txtfiles
  rm -f __files_to_zip
  rm -rf $PACKAGE_NAME
}

function create_main_file()
{
  echo " - Creating single tar.gz file - "
  tar czf $BIG_TAR $PACKAGE_NAME/

  echo " - Creating single win zip - "
  # ZIP
  find $PACKAGE_NAME/ > __files_to_zip
  # zip binfiles
  file -f __files_to_zip | fgrep -f _txtfiles -v | cut -d: -f1 | zip -9q $BIG_ZIP -@
  #zip ascii files with win line endings
  file -f __files_to_zip | fgrep -f _txtfiles | cut -d: -f1 | zip -l9q $BIG_ZIP -@
}

function create_and_delete_submodule()
{
  mkdir submodules_tar
  mkdir submodules_zip
  while read submodule; do
    _file=$(echo "$submodule" | cut -d'/' -f1)-$QTVER
    echo " - tarring $_file -"
    tar czf $_file.tar.gz $PACKAGE_NAME/$submodule
    mv $_file.tar.gz submodules_tar/
    find $PACKAGE_NAME/$submodule > __files_to_zip
    echo "- zippinging $_file -"
    # zip binfiles
    file -f __files_to_zip | fgrep -f _txtfiles -v | cut -d: -f1 | zip -9q $_file.zip -@
    #zip ascii files with win line endings
    file -f __files_to_zip | fgrep -f _txtfiles | cut -d: -f1 | zip -l9q $_file.zip -@
    mv $_file.zip submodules_zip/
    rm -rf $PACKAGE_NAME/$submodule
  done < $MODULES
}

#read machine config
. $(dirname $0)/default_src.config

# check that qmake can be found from path for generating docs
export PATH=$QDOC_PATH:$PATH
export LD_LIBRARY_PATH=$QDOC_LIBS:$LD_LIBRARY_PATH
qmake -v >/dev/null 2>&1 || tool_failure

# read the arguments
while test $# -gt 0; do
  case "$1" in
    -h|--help)
      usage
      exit 0
    ;;
    -m|--modules)
      shift
      MULTIPACK=yes
    ;;
    -d|--no_docs)
      shift
      DOCS=skip
    ;;    -u|--url)
      shift
      REPO_DIR=/$1
      if [ ! -d "$REPO_DIR/.git" ]; then
        echo "Error: $REPO_DIR is not a valid git repo ($1)"
        exit 1
      fi
      shift
    ;;
    -v|--version)
      shift
      QTVER=$1
      QTSHORTVER=$(echo $QTVER | cut -d. -f1-2)
      shift
    ;;
    *)
      echo "Error: Unknown option $1"
      usage
      exit 0
    ;;
    esac
done
if [ ! -d "$REPO_DIR/.git" ]; then
  echo "$REPO_DIR is not a valid git repo"
  exit 2
fi

PACKAGE_NAME=qt-everywhere-opensource-src-$QTVER
BIG_TAR=$PACKAGE_NAME.tar.gz
BIG_ZIP=$PACKAGE_NAME.zip
MODULES=submodules.txt
_TMP_DIR=$CUR_DIR/$PACKAGE_NAME

#------------------------------------------------------------------
# Step 1, Find all submodules from main repo and archive them
#------------------------------------------------------------------

echo " -- Finding submodules from $REPO_DIR -- "

rm -f $MODULES
rm -f $BIG_TAR
rm -f $BIG_ZIP
rm -rf $_TMP_DIR
mkdir $_TMP_DIR

cd $REPO_DIR

# detect the submodules to be archived
rm -f $MODULES
find . -name '.git' -type d -print | sed -e 's/^\.\///' -e 's/\.git$//' | grep -v '^$' >> $MODULES

#archive the main repo
git archive --format=tar  HEAD | gzip -4 > $CUR_DIR/$BIG_TAR
mv $CUR_DIR/$BIG_TAR $_TMP_DIR
cd $_TMP_DIR
tar xzf $BIG_TAR
rm -f $BIG_TAR
cd $REPO_DIR
echo "Qt main repo sha1:" >$_TMP_DIR/$QTGITTAG
cat .git/refs/heads/master >> $_TMP_DIR/$QTGITTAG
echo "-----------------------------------------" >> $_TMP_DIR/$QTGITTAG

#archive all the submodules and generate file from sha1's
while read submodule; do
  echo " -- From dir $PWD/$submodule, lets pack $submodule --"
  cd $submodule
  _file=$(echo "$submodule" | cut -d'/' -f1).tar.gz
  #archive submodule to $CUR_DIR/$BIG_TAR
  git archive --format=tar --prefix=$submodule/ HEAD | gzip -4 > $CUR_DIR/$_file
  #move it temp dir
  mv $CUR_DIR/$_file $_TMP_DIR
  #store the sha1
  echo "$submodule sha1:" >> $_TMP_DIR/$QTGITTAG
  _SHA=`cat .git/HEAD | cut -d' ' -f2`
  if [ $_SHA = refs/heads/master ]; then
    cat .git/refs/heads/master >> $_TMP_DIR/$QTGITTAG
  else
    cat .git/HEAD >> $_TMP_DIR/$QTGITTAG
  fi
  echo "-----------------------------------------" >> $_TMP_DIR/$QTGITTAG
  cd $_TMP_DIR
  #extract to tmp dir
  tar xzf $_file
  rm -f $_file
  cd $REPO_DIR
done < $MODULES
mv $MODULES $CUR_DIR
#------------------------------------------------------------------
# Step x,  remove rest of the unnecessary files   TODO
#------------------------------------------------------------------

#------------------------------------------------------------------
# Step x,  replace version strings with correct version, and
# patch Qt_PACKAGE_TAG and QT_PACKAGEDATE_STR defines
#------------------------------------------------------------------
echo " -- Patching %VERSION% etc. defines --"
cd $CUR_DIR/$PACKAGE_NAME/
find . -type f -print0 | xargs -0 sed -i -e "s/%VERSION%/$QTVER/g" -e "s/%SHORTVERSION%/$QTSHORTVER./g" -e "s/#define QT_PACKAGE_TAG \"\"/#define QT_PACKAGE_TAG \"\"/g" -e "s/#define QT_PACKAGEDATE_STR \"YYYY-MM-DD\"/#define QT_PACKAGEDATE_STR \"$PACK_TIME\"/g"

#------------------------------------------------------------------
# Step x,  generate docs TODO
#------------------------------------------------------------------
if [ $DOCS = generate ]; then
  echo " -- Creating online documentation -- "
  cd $CUR_DIR/$PACKAGE_NAME/qtdoc
  qmake
  make online_docs
else
  echo " -- Creating src files without generated online documentation --"
fi

#------------------------------------------------------------------
# Step x,  create zip file and tar files
#------------------------------------------------------------------
# list text file regexp keywords, if you find something obvious missing, feel free to add
cd $CUR_DIR
echo "ASCII
directory
empty
POSIX
html
text" > _txtfiles

echo " -- Create B I G tars -- "
create_main_file

if [ $MULTIPACK=yes ]; then
  mv $BIG_TAR $BIG_TAR.huge
  mv $BIG_ZIP $BIG_ZIP.huge
  echo " -- Creating tar per submodule -- "
  create_and_delete_submodule
  create_main_file
  mv $BIG_TAR submodules_tar/qt5-$QTVER.tar.gz
  mv $BIG_ZIP submodules_zip/qt5-$QTVER.zip
  mv $BIG_TAR.huge $BIG_TAR
  mv $BIG_ZIP.huge $BIG_ZIP
fi
cleanup

echo "Done!"




