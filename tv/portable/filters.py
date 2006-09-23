import tabs
import feed
import folder
import playlist
import guide

# Returns items that match search
def matchingItems(obj, searchString):
    if searchString is None:
        return True
    searchString = searchString.lower()
    if searchString in obj.getTitle().lower():
        return True
    if searchString in obj.getDescription().lower():
        return True
    if not obj.isContainerItem:
        parent = obj.getParent()
        if searchString in parent.getTitle().lower():
            return True
        if searchString in parent.getDescription().lower():
            return True
    return False

def downloadingItems(obj):
    return obj.getState() == 'downloading'

def unwatchedItems(obj):
    return obj.getState() == 'newly-downloaded' and not obj.isNonVideoFile()

def expiringItems(obj):
    return obj.getState() == 'expiring' and not obj.isNonVideoFile()

def newWatchableItems(obj):
    return (obj.getState() in ('expiring','newly-downloaded')) and not obj.isNonVideoFile()

def watchableItems(obj):
    return (obj.isDownloaded() and not obj.isNonVideoFile() and 
            not obj.isContainerItem)

# This is "new" for the channel template, expect it to be
# updated frequently in the next couple of weeks --NN 9/11/06
def newItems(obj):
    return not obj.getViewed()

# Return True if a tab should be shown for obj in the frontend. The filter
# used on the database to get the list of tabs.
def mappableToTab(obj):
    return ((isinstance(obj, feed.Feed) and obj.isVisible()) or
            obj.__class__ in (tabs.StaticTab,
                folder.ChannelFolder, playlist.SavedPlaylist,
                folder.PlaylistFolder, guide.ChannelGuide))

def autoDownloads(item):
    return item.getAutoDownloaded() and item.getState() == 'downloading'

def manualDownloads(item):
    return not item.getAutoDownloaded() and not item.isPendingManualDownload() and item.getState() == 'downloading'
