from base64 import b64encode
from gtcache import gettext as _
from threading import RLock
import os
import re
import shutil

from database import DDBObject, defaultDatabase
from dl_daemon import daemon, command
from download_utils import nextFreeFilename, parseURL, shortenFilename
from util import getTorrentInfoHash
import app
import config
import httpclient
import indexes
import prefs
import random
import views
import flashscraper

# a hash of download ids that the server knows about.
_downloads = {}

# Returns an HTTP auth object corresponding to the given host, path or
# None if it doesn't exist
def findHTTPAuth(host,path,realm = None,scheme = None):
    #print "Trying to find HTTPAuth with host %s, path %s, realm %s, and scheme %s" %(host,path,realm,scheme)
    defaultDatabase.confirmDBThread()
    for obj in views.httpauths:
        if (obj.host == host and path.startswith(obj.path) and
            (realm is None or obj.realm == realm) and
            (scheme is None or obj.authScheme == scheme)):
            return obj
    return None


class HTTPAuthPassword(DDBObject):
    def __init__(self,username,password,host, realm, path, authScheme="Basic"):
        oldAuth = findHTTPAuth(host,path,realm,authScheme)
        while not oldAuth is None:
            oldAuth.remove()
            oldAuth = findHTTPAuth(host,path,realm,authScheme)
        self.username = username
        self.password = password
        self.host = host
        self.realm = realm
        self.path = os.path.dirname(path)
        self.authScheme = authScheme
        DDBObject.__init__(self)

    def getAuthToken(self):
        authString = ':'
        self.confirmDBThread()
        authString = self.username+':'+self.password
        return b64encode(authString)

    def getAuthScheme(self):
        self.confirmDBThread()
        return self.authScheme


def _getDownloader (dlid):
    return views.remoteDownloads.getItemWithIndex(indexes.downloadsByDLID, dlid)

def generateDownloadID():
    dlid = "download%08d" % random.randint(0,99999999)
    while _getDownloader (dlid=dlid):
        dlid = "download%08d" % random.randint(0,99999999)
    return dlid

class RemoteDownloader(DDBObject):
    """Download a file using the downloader daemon."""

    def __init__(self, url, item, contentType = None):
        self.origURL = self.url = url
        self.itemList = [item]
        self.dlid = generateDownloadID()
        self.status = {}
        if contentType is None:
            # HACK:  Some servers report the wrong content-type for torrent
            # files.  We try to work around that by assuming if the enclosure
            # states that something is a torrent, it's a torrent.
            # Thanks to j@v2v.cc
            enclosureContentType = item.getFirstVideoEnclosureType()
            if enclosureContentType == 'application/x-bittorrent':
                contentType = enclosureContentType
        self.contentType = ""
        self.deleteFiles = True
        self.channelName = None
        DDBObject.__init__(self)
        if contentType is None:
            self.contentType = ""
        else:
            self.contentType = contentType

        if self.contentType == '':
            self.getContentType()
        else:
            self.runDownloader()

    def signalChange (self, needsSave=True, needsSignalItem=True):
        if needsSignalItem:
            for item in self.itemList:
                item.signalChange(needsSave=False)
        DDBObject.signalChange (self, needsSave=needsSave)

    def onContentType (self, info):
        if not self.idExists():
            return

        if info['status'] == 200:
            self.url = info['updated-url']
            self.contentType = info.get('content-type',None)
            self.runDownloader()
        else:
            error = httpclient.UnexpectedStatusCode(info['status'])
            self.onContentTypeError(error)

    def onContentTypeError (self, error):
        if not self.idExists():
            return

        self.status['state'] = "failed"
        self.status['shortReasonFailed'] = error.getFriendlyDescription()
        self.status['reasonFailed'] = error.getLongDescription()
        self.signalChange()

    def getContentType(self):
        httpclient.grabHeaders(self.url, self.onContentType, self.onContentTypeError)
 
    @classmethod
    def initializeDaemon(cls):
        RemoteDownloader.dldaemon = daemon.ControllerDaemon()

    @classmethod
    def updateStatus(cls, data):
        self = _getDownloader (dlid=data['dlid'])
        # print data
        if self is not None:
            try:
                if self.status == data:
                    return
            except Exception, e:
                # This is a known bug with the way we used to save fast resume
                # data
                print "WARNING exception when comparing status: %s" % e

            wasFinished = self.isFinished()
            self.status = data
            # Store the time the download finished
            finished = self.isFinished() and not wasFinished
            self.signalChange(needsSignalItem=not finished)
            if finished:
                for item in self.itemList:
                    item.onDownloadFinished()

    ##
    # This is the actual download thread.
    def runDownloader(self):
        flashscraper.tryScrapingURL(self.url, self._runDownloader)

    def _runDownloader(self, url, contentType = None):
        if not self.idExists():
            return # we got deleted while we were doing the flash scraping
        if contentType is not None:
            self.contentType = contentType
        if url is not None:
            self.url = url
            c = command.StartNewDownloadCommand(RemoteDownloader.dldaemon,
                                                self.url, self.dlid, self.contentType, self.channelName)
            c.send()
            _downloads[self.dlid] = self
        else:
            self.status["state"] = 'failed'
            self.status["shortReasonFailed"] = _('File not found')
            self.status["reasonFailed"] = _('Flash URL Scraping Error')
        self.signalChange()

    ##
    # Pauses the download.
    def pause(self, block=False):
        if _downloads.has_key(self.dlid):
            c = command.PauseDownloadCommand(RemoteDownloader.dldaemon,
                                             self.dlid)
            c.send()
        else:
            self.status["state"] = "paused"
            self.signalChange()

    ##
    # Stops the download and removes the partially downloaded
    # file.
    def stop(self, delete):
        if ((self.getState() in ['downloading','uploading'])):
            if _downloads.has_key(self.dlid):
                c = command.StopDownloadCommand(RemoteDownloader.dldaemon,
                                                self.dlid, delete)
                c.send()
                del _downloads[self.dlid]
        else:
            if delete:
                try:
                    filename = self.status['filename']
                    if os.path.isfile(filename):
                        os.remove (filename)
                    elif os.path.isdir(filename):
                        shutil.rmtree (filename)
                except:
                    pass
            self.status["state"] = "stopped"
            self.signalChange()

    ##
    # Continues a paused, stopped, or failed download thread
    def start(self):
        if self.getState() == 'failed':
            if _downloads.has_key (self.dlid):
                del _downloads[self.dlid]
            self.dlid = generateDownloadID()
            views.remoteDownloads.recomputeIndex(indexes.downloadsByDLID)
            self.status = {}
            if self.contentType == "":
                self.getContentType()
            else:
                self.runDownloader()
            self.signalChange()
        elif self.getState() in ('stopped', 'paused', 'offline'):
            if _downloads.has_key(self.dlid):
                c = command.StartDownloadCommand(RemoteDownloader.dldaemon,
                                                 self.dlid)
                c.send()
            else:
                self.status['state'] = 'downloading'
                self.restart()
                self.signalChange()

    def migrate(self, directory):
        if _downloads.has_key(self.dlid):
            c = command.MigrateDownloadCommand(RemoteDownloader.dldaemon,
                                               self.dlid, directory)
            c.send()
        else:
            # downloader doesn't have our dlid.  Move the file ourself.
            try:
                shortFilename = self.status['shortFilename']
            except KeyError:
                print """\
WARNING: can't migrate download because we don't have a shortFilename!
URL was %s""" % self.url
                return
            try:
                filename = self.status['filename']
            except KeyError:
                print """\
WARNING: can't migrate download because we don't have a filename!
URL was %s""" % self.url
                return
            if os.path.exists(filename):
                newfilename = os.path.join(directory,
                        shortFilename)
                newfilename = shortenFilename(newfilename)
                if newfilename == filename:
                    return
                newfilename = nextFreeFilename(newfilename)
                try:
                    shutil.move(filename, newfilename)
                except IOError, error:
                    print "WARNING: Error moving %s to %s (%s)" % (self.status['filename'],
                            newfilename, error)
                else:
                    self.status['filename'] = newfilename
            self.signalChange()
        for i in self.itemList:
            i.migrateChildren(directory)

    def setDeleteFiles(self, deleteFiles):
        self.deleteFiles = deleteFiles

    def setChannelName(self, channelName):
        if self.channelName is None:
            self.channelName = channelName

    ##
    # Removes downloader from the database and deletes the file.
    def remove(self):
        self.stop(self.deleteFiles)
        DDBObject.remove(self)

    def getType(self):
        """Get the type of download.  Will return either "http" or
        "bittorrent".
        """
        self.confirmDBThread()
        if self.contentType == 'application/x-bittorrent':
            return "bittorrent"
        else:
            return "http"

    ##
    # In case multiple downloaders are getting the same file, we can support
    # multiple items
    def addItem(self,item):
        if item not in self.itemList:
            self.itemList.append(item)

    def removeItem(self, item):
        self.itemList.remove(item)
        if len (self.itemList) == 0:
            self.remove()

    def getRate(self):
        self.confirmDBThread()
        return self.status.get('rate', 0)

    def getETA(self):
        self.confirmDBThread()
        return self.status.get('eta', 0)

    def getStartupActivity(self):
        self.confirmDBThread()
        activity = self.status.get('activity')
        if activity is None:
            return _("starting up")
        else:
            return activity

    ##
    # Returns the reason for the failure of this download
    # This should only be called when the download is in the failed state
    def getReasonFailed(self):
        if not self.getState() == 'failed':
            msg = "getReasonFailed() called on a non-failed downloader"
            raise ValueError(msg)
        self.confirmDBThread()
        return self.status['reasonFailed']

    def getShortReasonFailed(self):
        if not self.getState() == 'failed':
            msg = "getShortReasonFailed() called on a non-failed downloader"
            raise ValueError(msg)
        self.confirmDBThread()
        return self.status['shortReasonFailed']
    ##
    # Returns the URL we're downloading
    def getURL(self):
        self.confirmDBThread()
        return self.url

    ##    
    # Returns the state of the download: downloading, paused, stopped,
    # failed, or finished
    def getState(self):
        self.confirmDBThread()
        return self.status.get('state', 'downloading')

    def isFinished(self):
        return self.getState() in ('finished', 'uploading')

    ##
    # Returns the total size of the download in bytes
    def getTotalSize(self):
        self.confirmDBThread()
        return self.status.get('totalSize', -1)

    ##
    # Returns the current amount downloaded in bytes
    def getCurrentSize(self):
        self.confirmDBThread()
        return self.status.get('currentSize', 0)

    ##
    # Returns the filename that we're downloading to. Should not be
    # called until state is "finished."
    def getFilename(self):
        self.confirmDBThread()
        return self.status.get('filename', '')

    def onRestore(self):
        self.deleteFiles = True
        self.itemList = []
        if self.dlid == 'noid':
            # this won't happen nowadays, but it can for old databases
            self.dlid = generateDownloadID()
        self.status['rate'] = 0
        self.status['eta'] = 0

    def restartIfNeeded(self):
        if self.getState() in ('downloading','uploading','offline'):
            self.restart()

    def restart(self):
        if len(self.status) == 0 or self.status.get('dlerType') is None:
            if self.contentType == "":
                self.getContentType()
            else:
                self.runDownloader()
        else:
            _downloads[self.dlid] = self
            c = command.RestoreDownloaderCommand(RemoteDownloader.dldaemon, 
                                                 self.status)
            c.send()

def cleanupIncompleteDownloads():
    downloadDir = os.path.join(config.get(prefs.MOVIES_DIRECTORY),
            'Incomplete Downloads')
    if not os.path.exists(downloadDir):
        return

    filesInUse = set()
    views.remoteDownloads.confirmDBThread()
    for downloader in views.remoteDownloads:
        if downloader.getState() in ('downloading', 'paused', 'offline'):
            filename = downloader.getFilename()
            if len(filename) > 0:
                if not os.path.isabs(filename):
                    filename = os.path.join(downloadDir, filename)
                filesInUse.add(filename)

    for file in os.listdir(downloadDir):
        file = os.path.join(downloadDir, file)
        if file not in filesInUse:
            try:
                if os.path.isfile(file):
                    os.remove (file)
                elif os.path.isdir(file):
                    shutil.rmtree (file)
            except:
                pass # maybe a permissions error?  

def restartDownloads():
    views.remoteDownloads.confirmDBThread()
    for downloader in views.remoteDownloads:
        downloader.restartIfNeeded()

def startupDownloader():
    """Initialize the downloaders.

    This method currently does 2 things.  It deletes any stale files self in
    Incomplete Downloads, then it restarts downloads that have been restored
    from the database.  It must be called before any RemoteDownloader objects
    get created.
    """

    cleanupIncompleteDownloads()
    RemoteDownloader.initializeDaemon()
    restartDownloads()

def shutdownDownloader(callback = None):
    RemoteDownloader.dldaemon.shutdownDownloaderDaemon(callback=callback)

def lookupDownloader(url):
    return views.remoteDownloads.getItemWithIndex(indexes.downloadsByURL, url)

def getExistingDownloader(item):
    downloader = lookupDownloader(item.getURL())
    if downloader:
        downloader.addItem(item)
    return downloader

def getDownloader(item):
    existing = getExistingDownloader(item)
    if existing:
        return existing
    url = item.getURL()
    if url.startswith('file://'):
        scheme, host, port, path = parseURL(url)
        if re.match(r'/[a-zA-Z]:', path): 
            # fix windows pathnames (/C:/blah/blah/blah)
            path = path[1:]
        try:
            getTorrentInfoHash(path)
        except ValueError:
            raise ValueError("Don't know how to handle %s" % url)
        else:
            return RemoteDownloader(url, item, 'application/x-bittorrent')
    else:
        return RemoteDownloader(url, item)
