import feedparser
import logging

from opsdroid.skill import Skill
from opsdroid.matchers import match_crontab
from opsdroid.matchers import match_regex
from opsdroid.events import Message

_LOGGER = logging.getLogger(__name__)

class FeedreaderSkill(Skill):

    def __init__(self, opsdroid, config):
        super(FeedreaderSkill, self).__init__(opsdroid, config)

        # TODO: remove this. Load on demand and save on demand.
        self.subscriptions = dict()

    async def _load_subscriptions(self):
        self.subscriptions = await self.opsdroid.memory.get("feedreader-subscriptions", default=dict())

    async def _save_subscriptions(self):
        await self.opsdroid.memory.put("feedreader-subscriptions", self.subscriptions)

    def _new_bookmark(self, parsed_feed):
        latest_entry_date = ''
        for entry in parsed_feed.entries:
            latest_entry_date = entry.updated if entry.updated > latest_entry_date else latest_entry_date
        return latest_entry_date

    async def _get_feed(self, feed_url):
        return feedparser.parse(feed_url)

    def _get_new_entries_from_feed(self, parsed_feed, bookmark):
        new_entries = []
        for entry in parsed_feed.entries:
            if entry.updated > bookmark:
                new_entries.append(entry)
        return new_entries

    async def _handle_new_entries(self, new_entries, subscription_info):
        for new_entry in new_entries:
            connector = self.opsdroid.get_connector(subscription_info['connector'])
            target = subscription_info['target']
            message = self._create_new_entry_message(new_entry, connector, target)
            _LOGGER.debug(f"Sending message for {new_entry.title} to {target} via connector {connector.name}.")
            await connector.send(message)

    def create_new_entry_message(self, entry, connector, target):
        message = f"{entry.title}\n{entry.summary}\n{entry.link}"
        return Message(text=message,connector=connector, target=target)

    @match_regex(r'subscribe (.*)')
    async def subscribe(self, message):
        feed_url = message.regex.group(1)
        user = message.user
        _LOGGER.info(f"Subscribing {user} to {feed_url}")
        parsed_feed = await self._get_feed(feed_url)
        subscription_info = {
            "bookmark" : self._new_bookmark(parsed_feed),
            "connector" : message.connector.name,
            "feed_url" : feed_url,
            "target" : message.target
        }
        await self._load_subscriptions()
        if not user in self.subscriptions:
            self.subscriptions[user] = dict()
        self.subscriptions[user][feed_url] = subscription_info
        await self._save_subscriptions()
        await message.respond(f"Okay, I subscribed you to: {parsed_feed.feed.title}")

    @match_regex(r'unsubscribe (.*)')
    async def unsubscribe(self, message):
        user = message.user
        feed_url = message.regex.group(1)
        _LOGGER.info(f"Unsubscribing {user} from {feed_url}")
        await self._load_subscriptions()
        self.subscriptions[user].pop(feed_url)
        await self._save_subscriptions()
        await message.respond(f"I removed {feed_url} from your subscriptions.")

    @match_regex(r'list subscriptions')
    async def list_subscriptions(self, message):
        await self._load_subscriptions()
        user = message.user
        if user in self.subscriptions:
            user_subscriptions = self.subscriptions[user]
            response = ["Feeds:"]
            for feed, info in user_subscriptions.items():
                response.append(f"  {feed} (bookmark: {info['bookmark']})")
            await message.respond('\n'.join(response))
        else:
            await message.respond("No feeds on your list.")

    @match_crontab('0 * * * *', timezone="Europe/London")
    async def check_feeds(self, event):
        parsed_feeds = dict()
        await self._load_subscriptions()
        _LOGGER.info(f"Checking feeds ...")
        for user, user_subscriptions in self.subscriptions.items():
            _LOGGER.debug(f"Checking feeds for user {user} ...")
            for feed, info in user_subscriptions.items():
                if not (feed in parsed_feeds):
                    _LOGGER.debug(f"Fetching feed from {feed} ...")
                    parsed_feeds[feed] = await self._get_feed(feed)
                new_entries = self._get_new_entries_from_feed(parsed_feeds[feed], info['bookmark'])
                _LOGGER.debug(f"Feed {feed} for user {user} has {len(new_entries)} new entries ...")

                # send new entries to chat service
                await self._handle_new_entries(new_entries, info)

                # set new bookmark
                self.subscriptions[user][feed_url]['bookmark'] = self._new_bookmark(parsed_feeds[feed])
                await self._save_subscriptions()
