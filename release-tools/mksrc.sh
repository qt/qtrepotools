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
IGNORE_LIST=
LICENSE=opensource
PATCH_FILE=''

function usage()
{
  echo "Usage:"
  echo "./mksrc.sh -u <file_url_to_git_repo> -v <version> [-m][-d][-i sub][-l lic][-p patch]"
  echo "where -u is path to git repo and -v is version"
  echo "Optional parameters:"
  echo "-m             one is able to tar each sub module separately"
  echo "-no-docs       skip generating documentation"
  echo "-i submodule   will exclude the submodule from final package "
  echo "-l license     license type, will default to 'opensource', if set to 'commercial' all the necessary patches will be applied for commercial build"
  echo "-p patch file  patch file (.sh) to execute, example: change_licenses.sh"
}

function cleanup()
{
  echo "Cleaning all tmp artifacts"
  rm -f _txtfiles
  rm -f __files_to_zip
  rm -f _tmp_mod
  rm -f _tmp_shas
  rm -rf $PACKAGE_NAME
}

function create_main_file()
{
  echo " - Creating single tar.gz file - "
  tar czf $BIG_TAR $PACKAGE_NAME/

  echo " - Creating single tar.bz2 file - "
  tar cjf $PACKAGE_NAME.tar.bz2 $PACKAGE_NAME/

  echo " - Creating single tar.xz file - "
  tar cJf $PACKAGE_NAME.tar.xz $PACKAGE_NAME/

  echo " - Creating single 7z file - "
  7z a $PACKAGE_NAME.7z $PACKAGE_NAME/ > /dev/null

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
    --no-docs)
      shift
      DOCS=skip
    ;;
    -i|--ignore)
      shift
      IGNORE_LIST=$IGNORE_LIST" "$1
      shift
    ;;
    -u|--url)
      shift
      REPO_DIR=/$1
      shift
    ;;
    -v|--version)
      shift
        QTVER=$1
        QTSHORTVER=$(echo $QTVER | cut -d. -f1-2)
      shift
    ;;
    -l|--license)
      shift
      LICENSE=$1
      shift
    ;;
    -p|--patch_file)
      shift
      PATCH_FILE=$1
      shift
    ;;
    *)
      echo "Error: Unknown option $1"
      usage
      exit 0
    ;;
    esac
done

# Check if the DIR is valid git repository
if [ ! -d "$REPO_DIR/.git" ]; then
  echo "$REPO_DIR is not a valid git repo"
  exit 2
fi

PACKAGE_NAME=qt-everywhere-$LICENSE-src-$QTVER
BIG_TAR=$PACKAGE_NAME.tar.gz
BIG_ZIP=$PACKAGE_NAME.zip
MODULES=$CUR_DIR/submodules.txt
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
_SHA=`cat .git/refs/heads/master`
rm -f $_TMP_DIR/$QTGITTAG
echo "qt5=$_SHA">$_TMP_DIR/$QTGITTAG

#archive all the submodules and generate file from sha1's
while read submodule; do
  echo " -- From dir $PWD/$submodule, lets pack $submodule --"
  cd $submodule
  _file=$(echo "$submodule" | cut -d'/' -f1).tar.gz
  #archive submodule to $CUR_DIR/$_file
  git archive --format=tar --prefix=$submodule/ HEAD | gzip -4 > $CUR_DIR/$_file
  #move it temp dir
  mv $CUR_DIR/$_file $_TMP_DIR
  #store the sha1
  _SHA=`cat .git/HEAD | cut -d' ' -f2`
  if [ $(echo $_SHA | cut -d/ -f1-2) = refs/heads ]; then
    _SHA=`cat .git/$_SHA`
  else
    _SHA=`cat .git/HEAD`
  fi
  echo "$(echo $(echo $submodule|sed 's/-/_/g') | cut -d/ -f1)=$_SHA" >>$_TMP_DIR/$QTGITTAG
  cd $_TMP_DIR
  #extract to tmp dir
  tar xzf $_file
  rm -f $_file
  cd $REPO_DIR
done < $MODULES
#mv $MODULES $CUR_DIR

#------------------------------------------------------------------
# Step 2,  remove rest of the unnecessary files and ignored submodules
# and its sha1 values from sha file
#------------------------------------------------------------------
rm -f $CUR_DIR/$PACKAGE_NAME/init-repository
rm -f $CUR_DIR/$PACKAGE_NAME/.commit-template
rm -f $CUR_DIR/$PACKAGE_NAME/.gitmodules
find $CUR_DIR/$PACKAGE_NAME -name .gitignore -exec rm -f {} \; > /dev/null 2>&1
find $CUR_DIR/$PACKAGE_NAME -name .gitattributes -exec rm -f {} \; > /dev/null 2>&1
rm -f $CUR_DIR/$PACKAGE_NAME/qtbase/header.*
# find ./ -type d -name "tests" -exec rm -rf {} \; > /dev/null 2>&1

cd $CUR_DIR/$PACKAGE_NAME
__skip_sub=no
rm -f _tmp_mod
rm -f _tmp_shas

# read the shas
. $CUR_DIR/$PACKAGE_NAME/$QTGITTAG
echo "The qt5 was archived from $qt5 sha" >$CUR_DIR/_tmp_shas
echo "------------------------------------------------------------------------">>$CUR_DIR/_tmp_shas
echo "Fixing shas"
while read submodule; do
  for ignore in $IGNORE_LIST; do
    if [ _pre_$ignore"/" = _pre_$submodule ]; then
      __skip_sub=yes
      echo "removing $submodule"
      rm -rf $submodule
      break
    fi
  done
  if [ $__skip_sub = no ]; then
    __sub=$(echo $(echo $submodule|sed 's/-/_/g') | cut -d/ -f1)
    echo "Fixing $__sub ${!__sub}"
    echo $submodule >>$CUR_DIR/_tmp_mod
    echo "The $(echo $__sub| sed 's/_/-/g') was archived from ${!__sub} sha" >>$CUR_DIR/_tmp_shas
    echo "------------------------------------------------------------------------">>$CUR_DIR/_tmp_shas
  fi
  __skip_sub=no
done < $MODULES
cat $CUR_DIR/_tmp_mod > $MODULES
rm -f $CUR_DIR/$PACKAGE_NAME/$QTGITTAG
cat $CUR_DIR/_tmp_shas > $CUR_DIR/$PACKAGE_NAME/$QTGITTAG

#------------------------------------------------------------------
# Step 3,  replace version strings with correct version, and
# patch Qt_PACKAGE_TAG and QT_PACKAGEDATE_STR defines
#------------------------------------------------------------------
echo " -- Patching %VERSION% etc. defines --"
cd $CUR_DIR/$PACKAGE_NAME/
find . -type f -print0 | xargs -0 sed -i -e "s/%VERSION%/$QTVER/g" -e "s/%SHORTVERSION%/$QTSHORTVER/g" -e "s/#define QT_PACKAGE_TAG \"\"/#define QT_PACKAGE_TAG \"\"/g" -e "s/#define QT_PACKAGEDATE_STR \"YYYY-MM-DD\"/#define QT_PACKAGEDATE_STR \"$PACK_TIME\"/g"

#------------------------------------------------------------------
# Step 4,  generate docs
#------------------------------------------------------------------
if [ $DOCS = generate ]; then
  echo "DOC: Starting documentation generation.."
  # Make a copy of the source tree
  DOC_BUILD=$CUR_DIR/doc-build
  mkdir -p $DOC_BUILD
  echo "DOC: copying sources to $DOC_BUILD"
  cp -R $CUR_DIR/$PACKAGE_NAME $DOC_BUILD
  cd $DOC_BUILD/$PACKAGE_NAME
  # Build bootstrapped qdoc
  echo "DOC: configuring build"
  ./configure -developer-build -opensource -confirm-license -nomake examples -nomake tests -release -fast
  # Run qmake in each module, as this generates the .pri files that tell qdoc what docs to generate
  QMAKE=$PWD/qtbase/bin/qmake
  echo "DOC: running $QMAKE and qmake_all for submodules"
  for i in `cat $MODULES` ; do if [ -d $i -a -e $i/*.pro ] ; then (cd $i ; $QMAKE ; make -j12 qmake_all ) ; fi ; done
  # Build libQtHelp.so and qhelpgenerator
  echo "DOC: Build libQtHelp.so and qhelpgenerator"
  (cd qtbase && make -j12)
  (cd qtxmlpatterns ; make -j12)
  (cd qttools ; make -j12)
  (cd qttools/src/assistant/help ; make -j12)
  (cd qttools/src/assistant/qhelpgenerator ; make -j12)
  # Generate the offline docs and qt.qch
  echo "DOC: Generate the offline docs and qt.qch"
  (cd qtdoc ; $QMAKE ; make -j12 qmake_all ; LD_LIBRARY_PATH=$PWD/../qttools/lib make -j12 qch_docs)
  # Put the generated docs back into the clean source directory
  echo "DOC: Put the generated docs back into the clean source directory"
  mv $DOC_BUILD/$PACKAGE_NAME/qtdoc/doc/html $CUR_DIR/$PACKAGE_NAME/qtdoc/doc
  mv $DOC_BUILD/$PACKAGE_NAME/qtdoc/doc/qch $CUR_DIR/$PACKAGE_NAME/qtdoc/qch
  # Cleanup
  cd $CUR_DIR/$PACKAGE_NAME/
  #rm -rf $DOC_BUILD
else
  echo " -- Creating src files without generated offline documentation --"
fi

#------------------------------------------------------------------
# Step 5,  check which license type is selected, and run patches
# if needed
#------------------------------------------------------------------
if [ $PATCH_FILE ]; then
  if [ $LICENSE = commercial ]; then
    # when doing commercial build, patch file needs src folder and qt version no as parameters
    $PATCH_FILE $CUR_DIR/$PACKAGE_NAME/ $QTVER
  else
    $PATCH_FILE
  fi
fi

#------------------------------------------------------------------
# Step 6,  create zip file and tar files
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

# Create tar/submodule
if [ $MULTIPACK = yes ]; then
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




