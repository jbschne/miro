# Miro - an RSS based video player application
# Copyright (C) 2005-2008 Participatory Culture Foundation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#
# In addition, as a special exception, the copyright holders give
# permission to link the code of portions of this program with the OpenSSL
# library.
#
# You must obey the GNU General Public License in all respects for all of
# the code used other than OpenSSL. If you modify file(s) with this
# exception, you may extend this exception to your version of the file(s),
# but you are not obligated to do so. If you do not wish to do so, delete
# this exception statement from your version. If you delete this exception
# statement from all source files in the program, then also delete it here.

"""linkhandler.py -- Handle links clicked on in the browser.  """

from miro import app
from miro import feed
from miro import filetypes
from miro import messages
from miro import subscription
from miro import guide

def handle_subscription_link(url):
    type, subscribeURLs = subscription.findSubscribeLinks(url)
    normalizedURLs = []
    for url, additional in subscribeURLs:
        normalized = feed.normalizeFeedURL(url)
        if feed.validateFeedURL(normalized):
            normalizedURLs.append((normalized, additional))
    if normalizedURLs:
        if type == 'feed':
            for url, additional in normalizedURLs:
                trackback = additional.get('trackback')
                messages.NewChannel(url, trackback).send_to_backend()
        elif type == 'download':
            for url, additional in normalizedURLs:
                print 'should download: ', url
                #singleclick.addDownload(url, additional)
        elif type == 'guide':
            for url, additional in normalizedURLs:
                print 'should add guide: ', url
                #if guide.getGuideByURL (url) is None:
                    #guide.ChannelGuide(url, [u'*'])
        else:
            raise AssertionError("Unknown subscribe type")
        return

def handle_external_url(url):
    if url.startswith(u'feed://'):
        feed_info = app.tab_list_manager.feed_list.find_feed_with_url(url)
        if feed_info is not None:
            print 'SHOULD BLINK: %r' % feed_info.name
        return

    if filetypes.isFeedFilename(url):
        ask_for_feed_subscribe(url)
    elif filetypes.isAllowedFilename(url): # media URL, download it
        # FIXME - implement download video
        print "SHOULD DOWNLOAD URL: ", url
    else:
        app.widgetapp.open_url(url)

def ask_for_feed_subscribe(url):
    url = feed.normalizeFeedURL(url)
    title = _("Subscribe to Feed")
    text = _("""This link appears to be a feed.  Do you want to \
add it to your subscriptions?

%s""") % url
    choices = (dialogs.BUTTON_YES, dialogs.BUTTON_NO)
    ret = dialogs.show_choice_dialog(title, description, choices)
    if ret == dialogs.BUTTON_YES:
        messages.NewChannel(url).send_to_backend()
