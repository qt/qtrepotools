// Copyright (C) 2016 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only

#include <qcoreapplication.h>
#include <qdiriterator.h>
#include <qfile.h>
#include <qmetaobject.h>
#include <qstring.h>
#include <qtextstream.h>
#include <qcommandlineparser.h>

#include <qdebug.h>

#include <limits.h>
#include <stdio.h>

static bool printFilename = true;
static bool modify = false;

static QByteArray signature(const QByteArray &line, int pos)
{
    int start = pos;
    // find first open parentheses
    while (start < line.length() && line.at(start) != '(')
        ++start;
    int i = ++start;
    int par = 1;
    // find matching closing parentheses
    while (i < line.length() && par > 0) {
        if (line.at(i) == '(')
            ++par;
        else if (line.at(i) == ')')
            --par;
        ++i;
    }
    if (par == 0)
        return line.mid(start, i - start - 1);
    return QByteArray();
}

static bool isValidIdentifierChar(char c)
{
    return c == '_' || QChar::isLetterOrNumber(uchar(c));
}

static bool checkSignature(const QString &fileName, QByteArray &line, const char *sig)
{
    static QStringList fileList;

    const int siglen = strlen(sig);
    int idx = -1;
    bool found = false;
    while ((idx = line.indexOf(sig, ++idx)) != -1) {
        if (idx > 0 && isValidIdentifierChar(line.at(idx - 1)))
            continue;
        int endIdx = idx + siglen;
        if (endIdx < line.length() && isValidIdentifierChar(line.at(endIdx)))
            continue;
        const QByteArray sl = signature(line, idx);
        QByteArray nsl(QMetaObject::normalizedSignature(sl.constData()));
        if (sl != nsl) {
            found = true;
            if (printFilename && !fileList.contains(fileName)) {
                fileList.prepend(fileName);
                printf("%s\n", fileName.toLocal8Bit().constData());
            }
            if (modify)
                line.replace(sl, nsl);
            //qDebug("expected '%s', got '%s'", nsl.data(), sl.data());
        }
    }
    return found;
}

void writeChanges(const QString &fileName, const QStringList &lines)
{
    QFile file(fileName);
    if (!file.open(QIODevice::WriteOnly)) {
        qDebug("unable to open file '%s' for writing (%s)", fileName.toLocal8Bit().constData(), file.errorString().toLocal8Bit().constData());
        return;
    }
    QTextStream stream(&file);
    for (int i = 0; i < lines.count(); ++i)
        stream << lines.at(i);
    file.close();
}

void check(const QString &fileName)
{
    QFile file(fileName);
    if (!file.open(QIODevice::ReadOnly)) {
        qDebug("unable to open file: '%s' (%s)", fileName.toLocal8Bit().constData(), file.errorString().toLocal8Bit().constData());
        return;
    }
    QStringList lines;
    bool found = false;
    while (true) {
        QByteArray line = file.readLine();
        if (line.isEmpty())
            break;
        found |= checkSignature(fileName, line, "SLOT");
        found |= checkSignature(fileName, line, "SIGNAL");
        if (modify)
            lines << line;
    }
    file.close();

    if (found && modify) {
        printf("Modifying file: '%s'\n", fileName.toLocal8Bit().constData());
        writeChanges(fileName, lines);
    }
}

void traverse(const QString &path)
{
    auto needsChecking = [] (QStringView path) {
        // list of file extensions that
        constexpr char extensions[][5] = {
            // C++ impl files:
            "C", // will also match .c (because we're matching case-insensitively, but ¯\_(ツ)_/¯
            "cpp",
            "cxx",
            "c++",
            // header files:
            "h",
            "hpp",
            "hxx",
            // Obj-C++ impl:
            "mm",
            // Parser generators:
            "g",
            // documentation may also include SIGNAL/SLOT macros:
            "qdoc",
        };

        // treat .in files like the underlying file
        if (path.endsWith(QLatin1StringView{".in"}, Qt::CaseInsensitive))
            path = path.chopped(3);

        for (const char *extension : extensions) {
            const QLatin1StringView ext{extension};
            if (path.endsWith(ext, Qt::CaseInsensitive) &&
                path.chopped(ext.size()).endsWith(u'.'))
            {
                return true;
            }
        }
        return false;
    };

    QDirIterator dirIterator(path, QDir::NoDotAndDotDot | QDir::Dirs | QDir::Files | QDir::NoSymLinks);

    while (dirIterator.hasNext()) {
        QString filePath = dirIterator.next();
        if (needsChecking(filePath))
            check(filePath);
        else if (QFileInfo(filePath).isDir())
            traverse(filePath); // recurse
    }
}

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);

    QCommandLineParser parser;
    parser.setApplicationDescription(
            QStringLiteral("Qt Normalize tool (Qt %1)\nOutputs all filenames that contain non-normalized SIGNALs and SLOTs")
            .arg(QString::fromLatin1(QT_VERSION_STR)));
    parser.addHelpOption();
    parser.addVersionOption();

    QCommandLineOption modifyOption(QStringLiteral("modify"),
                                    QStringLiteral("Fix all occurrences of non-normalized SIGNALs and SLOTs."));
    parser.addOption(modifyOption);

    parser.addPositionalArgument(QStringLiteral("path"),
                                 QStringLiteral("can be a single file or a directory (in which case, look for file types that may contain SIGNALs and SLOTs recursively)"));

    parser.process(app);

    if (parser.positionalArguments().count() != 1)
        parser.showHelp(1);
    QString path = parser.positionalArguments().first();
    if (path == "-")
        parser.showHelp(1);

    if (parser.isSet(modifyOption)) {
        printFilename = false;
        modify = true;
    }

    QFileInfo fi(path);
    if (fi.isFile()) {
        check(path);
    } else if (fi.isDir()) {
        if (!path.endsWith("/"))
            path.append("/");
        traverse(path);
    } else {
        qWarning("Don't know what to do with '%s'", path.toLocal8Bit().constData());
        return 1;
    }

    return 0;
}
