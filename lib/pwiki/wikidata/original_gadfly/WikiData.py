"""

Used terms:
    
    wikiword -- a string matching one of the wiki word regexes
    wiki page -- real existing content stored and associated with a wikiword
            (which is the page name). Sometimes page is synonymous for page name
    alias -- wikiword without content but associated to a page name.
            For the user it looks as if the content of the alias is the content
            of the page for the associated page name
    defined wiki word -- either a page name or an alias
"""



from os.path import exists, join, basename
import os, os.path

from time import time, localtime
import datetime
import string, glob, traceback

try:
#     tracer.runctx('import gadfly', globals(), locals())
    import gadfly
except ImportError:
    import ExceptionLogger
    ExceptionLogger.logOptionalComponentException(
            "Initialize gadfly for original_gadfly/WikiData.py")
    gadfly = None
# finally:
#     pass

if gadfly is not None:
    import DbStructure
    from DbStructure import createWikiDB


import Consts
from pwiki.WikiExceptions import *   # TODO make normal import?
from pwiki import SearchAndReplace

from pwiki.StringOps import longPathEnc, longPathDec, utf8Enc, utf8Dec, BOM_UTF8, \
        fileContentToUnicode, loadEntireTxtFile, loadEntireFile, \
        writeEntireFile, Conjunction, iterCompatibleFilename, \
        getFileSignatureBlock, lineendToInternal, guessBaseNameByFilename, \
        createRandomString, pathDec


from ..BaseWikiData import BasicWikiData, FileWikiData

class WikiData(FileWikiData, BasicWikiData):
    "Interface to wiki data."
    def __init__(self, wikiDocument, dataDir, tempDir, app=None):
        # app is unused in this gadfly implementation

        self.dbType = "original_gadfly"

        self.wikiDocument = wikiDocument
        self.dataDir = dataDir
        self.connWrap = None
        self.cachedWikiPageLinkTermDict = None
        # tempDir is ignored

# Moved to DbStructure
#        
#        # Only if this is true, the database is called to commit.
#        # This is necessary for read-only databases
#        self.commitNeeded = False

#         dbName = self.wikiDocument.getWikiConfig().get("wiki_db", "db_filename",
#                 u"").strip()
#                 
#         if (dbName == u""):
        dbName = u"wikidb"

        try:
            conn = gadfly.gadfly(dbName, self.dataDir)
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        # If true, forces the editor to write platform dependent files to disk
        # (line endings as CR/LF, LF or CR)
        # If false, LF is used always
        self.editorTextMode = False

        self.connWrap = DbStructure.ConnectWrap(conn)
        try:
            self.pagefileSuffix = self.wikiDocument.getWikiConfig().get("main",
                    "db_pagefile_suffix", u".wiki")
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def reconnectDb(self):
        try:
            self.connWrap.close()
            conn = gadfly.gadfly("wikidb", self.dataDir)
            self.connWrap = DbStructure.ConnectWrap(conn)
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getDbFilenames(self):
        # Hackish way to get all gadfly files
        files = [f for fi in os.listdir(self.dataDir) if f.endswith("grl")]
        files.append(wikidb)

        return files


    def checkDatabaseFormat(self):
        return DbStructure.checkDatabaseFormat(self.connWrap)


    def connect(self):
        formatcheck, formatmsg = self.checkDatabaseFormat()

        if formatcheck == 2:
            # Unknown format
            raise WikiDataException, formatmsg

        # Update database from previous versions if necessary
        if formatcheck == 1:
            # Offer the chance to backup a database
            self.backupDatabase()

            try:
                DbStructure.updateDatabase(self.connWrap, self.dataDir,
                        self.pagefileSuffix)
            except:
                self.connWrap.rollback()
                raise

        lastException = None
        try:
            # Further possible updates   
            DbStructure.updateDatabase2(self.connWrap)
        except (IOError, OSError, ValueError), e:
            # Remember but continue
            lastException = DbWriteAccessError(e)

        try:
            # Set marker for database type
            self.wikiDocument.getWikiConfig().set("main", "wiki_database_type",
                    "original_gadfly")
        except (IOError, OSError, ValueError), e:
            # Remember but continue
            lastException = DbWriteAccessError(e)

        # create word caches
        self.cachedWikiPageLinkTermDict = None

        try:
#             # cache aliases
#             aliases = self.getAllAliases()
#             for alias in aliases:
#                 self.cachedWikiPageLinkTermDict[alias] = 2
#     
#             # Cache real words
#             for word in self.getAllDefinedWikiPageNames():
#                 self.cachedWikiPageLinkTermDict[word] = 1
#     
            self.cachedGlobalAttrs = None
            self.getGlobalAttributes()
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            try:
                self.connWrap.rollback()
            except (IOError, OSError, ValueError), e2:
                traceback.print_exc()
                raise DbReadAccessError(e2)
            raise DbReadAccessError(e)

        if lastException:
            raise lastException


    # ---------- Handling of relationships cache ----------

    def _getAllRelations(self):
        "get all of the relations in the db"
        relations = []
        try:
            data = self.connWrap.execSqlQuery("select word, relation "
                    "from wikirelations")
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        for row in data:
            relations.append((row[0], row[1]))
        return relations

    def getChildRelationships(self, wikiWord, existingonly=False,
            selfreference=True, withFields=()):
        """
        get the child relations of this word
        Function must work for read-only wiki.
        existingonly -- List only existing wiki words
        selfreference -- List also wikiWord if it references itself
        withFields -- Seq. of names of fields which should be included in
            the output. If this is not empty, tuples are returned
            (relation, ...) with ... as further fields in the order mentioned
            in withfields.

            Possible field names:
                "firstcharpos": position of link in page (may be -1 to represent
                    unknown). The Gadfly implementation always returns -1
                "modified": Modification date

        """
        # TODO moddateLookup should also process aliases?
        def moddateLookup(rel):
            result = self.getTimestamps(rel)[0]
            if result is None:
                return 0

            return result

        if withFields is None:
            withFields = ()

        addFields = ""
        converters = [lambda s: s]
        for field in withFields:
            if field == "firstcharpos":
                addFields += ", relation" # Dummy field
                converters.append(lambda s: -1)
            elif field == "modified":
                addFields += ", relation"
                converters.append(moddateLookup)

        sql = "select relation%s from wikirelations where word = ?" % addFields

        if len(withFields) == 0:
            try:
                children = self.connWrap.execSqlQuerySingleColumn(sql,
                        (wikiWord,))
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)

            if not selfreference:
                try:
                    children.remove(wikiWord)
                except ValueError:
                    pass

            if existingonly:
                children = [c for c in children if self.getWikiPageNameForLinkTerm(c)]
        else:
            try:
                children = self.connWrap.execSqlQuery(sql, (wikiWord,))
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)

            newChildren = []
            for c in children:
                newC = tuple((conv(item) for item, conv in zip(c, converters)))
                if not selfreference and newC[0] == wikiWord:
                    continue

                if existingonly and not self.getWikiPageNameForLinkTerm(newC[0]):
                    continue

                newChildren.append(newC)

            children = newChildren

        return children


#     # TODO More efficient
#     def _hasChildren(self, wikiWord, existingonly=False,
#             selfreference=True):
#         return len(self.getChildRelationships(wikiWord, existingonly,
#                 selfreference)) > 0
#                 
#     # TODO More efficient                
#     def getChildRelationshipsAndHasChildren(self, wikiWord, existingonly=False,
#             selfreference=True):
#         """
#         get the child relations to this word as sequence of tuples
#             (<child word>, <has child children?>). Used when expanding
#             a node in the tree control.
#         existingonly -- List only existing wiki words
#         selfreference -- List also wikiWord if it references itself
#         """
#         children = self.getChildRelationships(wikiWord, existingonly,
#                 selfreference)
#                 
#         return map(lambda c: (c, self._hasChildren(c, existingonly,
#                 selfreference)), children)

    def getParentRelationships(self, wikiWord):
        "get the parent relations to this word"

        # Parents of the real word
        realWord = self.getWikiPageNameForLinkTerm(wikiWord)
        if realWord is None:
            realWord = wikiWord
        try:
            parents = set(self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikirelations where relation = ?", (realWord,)))
            
            otherTerms = self.connWrap.execSqlQuery(
                    "select matchterm, type from wikiwordmatchterms "
                    "where word = ?", (realWord,))

            for matchterm, typ in otherTerms:
                if not typ & Consts.WIKIWORDMATCHTERMS_TYPE_ASLINK:
                    continue

                parents.update(self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikirelations where relation = ?",
                    (matchterm,)))

#             # Plus parents of aliases
#             aliases = [v for k, v in self.getAttributesForWord(wikiWord)
#                     if k == u"alias"]
#     
#             for al in aliases:
#                 parents.update(self.connWrap.execSqlQuerySingleColumn(
#                     "select word from wikirelations where relation = ?", (al,)))

        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        return list(parents)


    def getParentlessWikiWords(self):
        """
        get the words that have no parents.
        
        NO LONGER VALID: (((also returns nodes that have files but
        no entries in the wikiwords table.)))
        """
        wordSet = set(self.getAllDefinedWikiPageNames())

        # Remove all which have parents
        for word, relation in self._getAllRelations():
            relation = self.getWikiPageNameForLinkTerm(relation)
            if relation is None or word == relation:
                continue

            wordSet.discard(relation)

        # Create a list of them
        words = list(wordSet)
#         words.sort()
        
        return words


    def getUndefinedWords(self):
        """
        List words which are childs of a word but are not defined, neither
        directly nor as alias.
        """
        relations = self._getAllRelations()
        childWords = set([relation for word, relation in relations])
        
        return [word for word in childWords
                if not self.getWikiPageNameForLinkTerm(word)]


    def _addRelationship(self, word, rel):
        """
        Add a relationship from word to rel. rel is a tuple (toWord, pos).
        The Gadfly implementation ignores pos.
        Returns True if relation added.
        A relation from one word to another is unique and can't be added twice.
        """
        try:
            data = self.connWrap.execSqlQuery("select relation "
                    "from wikirelations where word = ? and relation = ?",
                    (word, rel[0]))
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        returnValue = False
        if len(data) < 1:
            try:
                self.connWrap.execSqlInsert("wikirelations", ("word", "relation",
                        "created"), (word, rel[0], time()))
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbWriteAccessError(e)

            returnValue = True
        return returnValue

    def findBestPathFromWordToWord( self, word, toWord ):
        """
        Do a breadth first search, which will find the shortest
        path between the nodes we are interested in
        This will only find a path if the words are
        linked by parent relationship
        you won't be able to find your cousins
        """
        queue = [word]
        previous = { word: word }
        while queue:
            node = queue.pop(0)
            if node == toWord: # If we've found our target word.
                # Work out how we got here.
                path = [node]
                while previous[node] != node:
                    node = previous[node]
                    path.append( node )
                return path

            # Continue on up the tree.
            for parent in self.getParentRelationships(node):
                # unless we've been to this parent before.
                if parent not in previous and parent not in queue:
                    previous[parent] = node
                    queue.append( parent )

        # We didn't find a path to our target word
        return None




    # ---------- Listing/Searching wiki words (see also "alias handling", "searching pages")----------

#     def getAllDefinedWikiPageNames(self):
#         "get the names of all wiki pages in the db, no aliases"
#         wikiWords = self.getAllDefinedWikiPageNames()
#         # Filter out functional 'words'
#         wikiWords = [w for w in wikiWords if not w.startswith('[')]
#         return wikiWords

    def getDefinedWikiPageNamesStartingWith(self, thisStr):
        """
        Get the names of all wiki pages in the db starting with  thisStr
        Function must work for read-only wiki.
        """
        try:
            words = self.getAllDefinedWikiPageNames()
            return [w for w in words if w.startswith(thisStr)]

        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def _findNewWordForFile(self, path):
        wikiWord = guessBaseNameByFilename(path, self.pagefileSuffix)
        try:
            if self.connWrap.execSqlQuerySingleItem(
                    "select word from wikiwords where word = ?", (wikiWord,)):
                for i in range(20):    # "while True" is too dangerous
                    rand = createRandomString(10)
                    
                    if self.connWrap.execSqlQuerySingleItem(
                            "select word from wikiwords where word = ?",
                            (wikiWord + u"~" + rand,)):
                        continue
                    
                    return wikiWord + u"~" + rand

                return None

            else:
                return wikiWord

        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def _getCachedWikiPageLinkTermDict(self):
        # TODO: case insensitivity
        try:
            if self.cachedWikiPageLinkTermDict is None:
                result = {}
        
                for matchterm, typ, word in self.connWrap.execSqlQuery(
                        "select matchterm, type, word from wikiwordmatchterms"):
    
                    if not (typ & Consts.WIKIWORDMATCHTERMS_TYPE_ASLINK):
                        continue
                    result[matchterm] = word

                result.update((word, word)
                        for word in self.getAllDefinedWikiPageNames())

                self.cachedWikiPageLinkTermDict = result

            return self.cachedWikiPageLinkTermDict
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getWikiPageLinkTermsStartingWith(self, thisStr, caseNormed=False):
        """
        Get the list of wiki page link terms (page names or aliases)
        starting with thisStr. Used for autocompletion.
        """
        if caseNormed:
            thisStr = thisStr.lower()   # TODO More general normcase function

            try:
                foundTerms = self.connWrap.execSqlQuery(
                        "select matchterm, type "
                        "from wikiwordmatchterms")

            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)

            return [matchterm for matchterm, typ in foundTerms
                    if matchterm.lower().startswith(thisStr) and
                    (typ & Consts.WIKIWORDMATCHTERMS_TYPE_ASLINK)]

        else:
            try:
                foundTerms = self.connWrap.execSqlQuery(
                        "select matchterm, type "
                        "from wikiwordmatchterms")
                
                words = set(matchterm for matchterm, typ in foundTerms
                        if matchterm.startswith(thisStr) and
                        (typ & Consts.WIKIWORDMATCHTERMS_TYPE_ASLINK))

                # To ensure that at least all real wikiwords are found
                
                realWords = self.connWrap.execSqlQuerySingleColumn("select word "
                    "from wikiwords")
                
                words.update(word for word in realWords
                        if word.startswith(thisStr))
                        
                return list(words)

                
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)

        
        
#         words = self.getAllDefinedWikiPageNames()
#         if includeAliases:
#             words.extend(self.getAllAliases())
#         
#         if caseNormed:
#             thisStr = thisStr.lower()   # TODO More general normcase function
#             startingWith = [word for word in words
#                     if word.lower().startswith(thisStr)]
#             return startingWith
#         else:
#             startingWith = [word for word in words if word.startswith(thisStr)]
#             return startingWith



#     def getWikiWordsWith(self, thisStr, includeAliases=False):
#         "get the list of words with thisStr in them."
#         thisStr = thisStr.lower()   # TODO More general normcase function
# 
# 
#         result1 = [word for word in self.getAllDefinedWikiPageNames()
#                 if word.lower().startswith(thisStr)]
# 
#         if includeAliases:
#             result1 += [word for word in self.getAllAliases()
#                     if word.lower().startswith(thisStr)]
# 
# 
#         result2 = [word for word in self.getAllDefinedWikiPageNames()
#                 if word.lower().find(thisStr) != -1 and
#                 not word.lower().startswith(thisStr)]
# 
#         if includeAliases:
#             result2 += [word for word in self.getAllAliases()
#                     if word.lower().find(thisStr) != -1 and
#                     not word.lower().startswith(thisStr)]
# 
#         coll = self.wikiDocument.getCollator()
#         
#         coll.sort(result1)
#         coll.sort(result2)
# 
#         return result1 + result2


    def getWikiPageNamesModifiedWithin(self, startTime, endTime):
        """
        Function must work for read-only wiki.
        startTime and endTime are floating values as returned by time.time()
        startTime is inclusive, endTime is exclusive
        """
        try:
            rows = self.connWrap.execSqlQuery("select word, modified "
                    "from wikiwords")
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)
            
        return [row[0] for row in rows if float(row[1]) >= startTime and
                float(row[1]) < endTime and not row[0].startswith('[')]


    def getTimeMinMax(self, stampType):
        """
        Return the minimal and maximal timestamp values over all wiki words
        as tuple (minT, maxT) of float time values.
        A time value of 0.0 is not taken into account.
        If there are no wikiwords with time value != 0.0, (None, None) is
        returned.
        
        stampType -- 0: Modification time, 1: Creation, 2: Last visit
        """
        field = self._STAMP_TYPE_TO_FIELD.get(stampType)
        if field is None:
            # Visited not supported yet
            return (None, None)

        try:
            rows = self.connWrap.execSqlQuery("select word, %s from wikiwords" %
                    field)
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        minT = None
        maxT = None
        
        # Find initial record for setting min/max
        for i in xrange(len(rows)):
            row = rows[i]
            if row[0].startswith('[') or row[1] == 0.0:
                continue
            
            minT = row[1]
            maxT = row[1]
            break
        
        for i in xrange(i + 1, len(rows)):
            row = rows[i]
            if row[0].startswith('[') or row[1] == 0.0:
                continue

            minT = min(minT, row[1])
            maxT = max(maxT, row[1])
            
        return (minT, maxT)


    def getWikiPageNamesBefore(self, stampType, stamp, limit=None):
        """
        Get a list of tuples of wiki words and dates related to a particular
        time before stamp.
        
        stampType -- 0: Modification time, 1: Creation, 2: Last visit
        limit -- How much count entries to return or None for all
        """
        field = self._STAMP_TYPE_TO_FIELD.get(stampType)
        if field is None:
            # Visited not supported yet
            return []

        try:
            rows = self.connWrap.execSqlQuery("select word, %s from wikiwords" %
                    field)
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        rows = [row for row in rows if float(row[1]) < stamp and
                row[1] > 0]

        rows.sort(key=lambda row: row[1], reverse=True)

        if limit is not None:
            return rows[:limit]
        else:
            return rows


    def getWikiPageNamesAfter(self, stampType, stamp, limit=None):
        """
        Get a list of of tuples of wiki words and dates related to a particular
        time after OR AT stamp.
        
        stampType -- 0: Modification time, 1: Creation, 2: Last visit
        limit -- How much words to return or None for all
        """
        field = self._STAMP_TYPE_TO_FIELD.get(stampType)
        if field is None:
            # Visited not supported yet
            return []

        try:
            rows = self.connWrap.execSqlQuery("select word, %s from wikiwords" %
                    field)
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        rows = [row for row in rows if float(row[1]) >= stamp]

        rows.sort(key=lambda row: row[1])

        if limit is not None:
            return rows[:limit]
        else:
            return rows


    def getFirstWikiPageName(self):
        """
        Returns the name of the "first" wiki word. See getNextWikiPageName()
        for details. Returns either an existing wiki word or None if no
        wiki words in database.
        """
        words = self.getAllDefinedWikiPageNames()
        words.sort()
        if len(words) == 0:
            return None
        return words[0]


    def getNextWikiPageName(self, currWord):
        """
        Returns the "next" wiki word after currWord or None if no
        next word exists. If you begin with the first word returned
        by getFirstWikiPageName() and then use getNextWikiPageName() to
        go to the next word until no more words are available
        and if the list of existing wiki words is not modified during
        iteration, it is guaranteed that you have visited all real
        wiki words (no aliases) then.
        currWord  doesn't have to be an existing word itself.
        Function must work for read-only wiki.
        """
        words = self.getAllDefinedWikiPageNames()
        words.sort()
        if len(words) == 0:
            return None
            
        try:
            i = words.index(currWord)
            i += 1
            if i == len(words):
                return None
            
            return words[i]
        except ValueError:
            None


    # ---------- Attribute cache handling ----------

    def getAttributeNames(self):
        """
        Return all attribute names not beginning with "global."
        """
        try:
            names = self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(key) from wikiwordattrs")
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        return [name for name in names if not name.startswith('global.')]

    def getAttributeNamesStartingWith(self, startingWith):
        try:
            names = self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(key) from wikiwordattrs")   #  order by key")
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        return [name for name in names if name.startswith(startingWith)]

    def getGlobalAttributes(self):
        if self.cachedGlobalAttrs is None:
            try:
                data = self.connWrap.execSqlQuery(
                        "select key, value from wikiwordattrs")  # order by key
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)
            globalMap = {}
            for (key, val) in data:
                if key.startswith('global.'):
                    globalMap[key] = val
            self.cachedGlobalAttrs = globalMap

        return self.cachedGlobalAttrs


    def _setAttribute(self, word, key, value):
        # make sure the value doesn't already exist for this attribute
        try:
            data = self.connWrap.execSqlQuery("select word from wikiwordattrs "
                    "where word = ? and key = ? and value = ?",
                    (word, key, value))
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)
        # if it doesn't insert it
        returnValue = False
        if len(data) < 1:
            try:
                self.connWrap.execSqlInsert("wikiwordattrs", ("word", "key",
                        "value"), (word, key, value))
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbWriteAccessError(e)
            returnValue = True
        return returnValue


    # ---------- Todo cache handling ----------

    def _addTodo(self, word, todo):
        try:
            self.connWrap.execSqlInsert("todos", ("word", "key", "value"),
                    (word, todo[0], todo[1]))

#             self.execSql("insert into todos(word, todo) values (?, ?)", (word, todo))
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    # ---------- Wikiword matchterm cache handling ----------

    def getWikiWordMatchTermsWith(self, thisStr, orderBy=None, descend=False):
        "get the list of match terms with thisStr in them."
        thisStr = thisStr.lower()   # TODO More general normcase function

        if orderBy == "visited":
            try:
                # TODO Check for name collisions
                foundTerms = self.connWrap.execSqlQuery(
                        "select matchterm, type, wikiwordmatchterms.word, "
                        "firstcharpos, charlength, visited "
                        "from wikiwordmatchterms, wikiwords "
                        "where wikiwordmatchterms.word = wikiwords.word")

            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)

        else:
            try:
                # TODO Check for name collisions
                foundTerms = self.connWrap.execSqlQuery(
                        "select matchterm, type, word, firstcharpos, charlength "
                        "from wikiwordmatchterms")
                
            except (IOError, OSError, ValueError), e:
                traceback.print_exc()
                raise DbReadAccessError(e)

        result1 = [term for term in foundTerms
            if term[0].lower().startswith(thisStr)]

        result2 = [term for term in foundTerms
                if term[0].lower().find(thisStr) > 0]

        coll = self.wikiDocument.getCollator()

        coll.sortByFirst(result1)
        coll.sortByFirst(result2)
        
        if orderBy == "visited":
            result1.sort(key=lambda k: k[5], reverse=descend)
            result2.sort(key=lambda k: k[5], reverse=descend)
        else:
            if descend:
                result1.reverse()
                result2.reverse()

        return result1 + result2


    def _addWikiWordMatchTerm(self, wwmTerm):
        matchterm, typ, word, firstcharpos, charlength = wwmTerm
        try:
            # TODO Check for name collisions
            self.connWrap.execSqlInsert("wikiwordmatchterms", ("matchterm",
                    "type", "word", "firstcharpos", "charlength"),
                    (matchterm, typ, word, firstcharpos, charlength))
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)

    def deleteWikiWordMatchTerms(self, word, syncUpdate=False):
        try:
            self.cachedWikiPageLinkTermDict = None
            foundTerms = self.connWrap.execSqlQuery(
                    "select matchterm, type, word, firstcharpos "
                    "from wikiwordmatchterms where word = ?", (word,))

            if not syncUpdate:
                for term in foundTerms:
                    if not term[1] & Consts.WIKIWORDMATCHTERMS_TYPE_SYNCUPDATE:
                        self.connWrap.execSql("delete from wikiwordmatchterms "
                                "where matchterm = ? and type = ? and word = ? and "
                                "firstcharpos = ?", term)

#                 self.connWrap.execSql("delete from wikiwordmatchterms "
#                         "where word = ?", (word,))
            else:
                for term in foundTerms:
                    if term[1] & Consts.WIKIWORDMATCHTERMS_TYPE_SYNCUPDATE:
                        self.connWrap.execSql("delete from wikiwordmatchterms "
                                "where matchterm = ? and type = ? and word = ? and "
                                "firstcharpos = ?", term)

        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    # ---------- Data block handling ----------

    def getDataBlockUnifNamesStartingWith(self, startingWith):
        """
        Return all unified names starting with startingWith (case sensitive)
        """
        try:
            names1 = self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(unifiedname) from datablocks")
                    
            names2 = self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(unifiedname) from datablocksexternal")
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        return [name for name in names1 if name.startswith(startingWith)] + \
                [name for name in names2 if name.startswith(startingWith)]



    # ---------- Miscellaneous ----------

    _CAPABILITIES = {
        "rebuild": 1,
        "filePerPage": 1   # Uses a single file per page
        }


    def clearCacheTables(self):
        """
        Clear all tables in the database which contain non-essential
        (cache) information as well as other cache information.
        Needed before rebuilding the whole wiki
        """
        self.cachedWikiPageLinkTermDict = None
        self.cachedGlobalAttrs = None
        try:
            self.fullyResetMetaDataState()
            self.commit()

            DbStructure.recreateCacheTables(self.connWrap)
            self.commit()
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def setDbSettingsValue(self, key, value):
        assert isinstance(value, basestring)
        DbStructure.setSettingsValue(self.connWrap, key, value)


    def getDbSettingsValue(self, key, default=None):
        return DbStructure.getSettingsValue(self.connWrap, key, default)


    def testWrite(self):
        """
        Test if writing to database is possible. Throws a DbWriteAccessError
        if writing failed.
        TODO !
        """
        pass



    # Not part of public API:

#     def execSql(self, sql, params=None):
#         "utility method, executes the sql, no return"
#         return self.connWrap.execSql(sql, params)
# 
#     def execSqlQuery(self, sql, params=None):
#         "utility method, executes the sql, returns query result"
#         return self.connWrap.execSqlQuery(sql, params)
# 
#     def execSqlQuerySingleColumn(self, sql, params=None):
#         "utility method, executes the sql, returns query result"
#         return self.connWrap.execSqlQuerySingleColumn(sql, params)


    # ---------- Other optional functionality ----------

    def cleanupAfterRebuild(self, progresshandler):
        """
        Rebuild cached structures, try to repair database inconsistencies.

        Must be implemented if checkCapability returns a version number
        for "rebuild".
        
        progresshandler -- Object, fulfilling the GuiProgressHandler
            protocol
        """
        DbStructure.rebuildIndices(self.connWrap)   # ?


####################################################
# module level functions
####################################################


# def findShortestPath(graph, start, end, path):   # path=[]
#     "finds the shortest path in the graph from start to end"
#     path = path + [start]
#     if start == end:
#         return path
#     if not graph.has_key(start):
#         return None
#     shortest = None
#     for node in graph[start]:
#         if node not in path:
#             newpath = findShortestPath(graph, node, end, path)
#             if newpath:
#                 if not shortest or len(newpath) < len(shortest):
#                     shortest = newpath
# 
#     return shortest



def listAvailableWikiDataHandlers():
    """
    Returns a list with the names of available handlers from this module.
    Each item is a tuple (<internal name>, <descriptive name>)
    """
    if gadfly is not None:
        return [("original_gadfly", "Original Gadfly")]
    else:
        return []


def getWikiDataHandler(name):
    """
    Returns a creation function (or class) for an appropriate
    WikiData object and a createWikiDB function or (None, None)
    if name is unknown
    """
    if name == "original_gadfly":
        return WikiData, createWikiDB
    
    return (None, None)
