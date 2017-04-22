#!/usr/bin/env python

import asyncio
import qrcode
import random
import re
import sys
import tempfile
import uuid

import PIL.Image
import ciscosparkapi

from spark import Server

validate_html = '''<html>
  <head>
    <style>
      h3 {{
        text-align: center;
      }}
      body {{
        background-color: {color};
      }}
    </style>
  </head>
  <body>
    <h3>{text}</h3>
  </body>
</html>'''


async def get_emails(spark, roomid):
    loop = asyncio.get_event_loop()
    members = await loop.run_in_executor(
        None,
        spark.memberships.list,
        roomid,
        None,
        None,
        1000)
    return [member.personEmail for member in members]


class Bongbot:
    def __init__(self, config):
        self._admins = config.get('administrators', [])
        self._ignore = config.get('ignore', [])
        self._bongs = config['bongs']
        self._draw = config.get('draw', None)
        self._validate_url = '{}/validate'.format(config['bot']['webhook'])

        self._setup_server(config)

        self._valid_bongs = {}
        self._people = {}
        self._validated = []

        self._background = None
        background = self._bongs.get('background', None)
        if background:
            self._background = PIL.Image.open(self._bongs['background'])
            self._foreground = PIL.Image.new(
                self._background.mode,
                self._background.size,
                'black',
            )

    async def party(self, loop, spark, message):
        if not self._allowed(message.personEmail):
            return

        self._server.listen(
            '^bong$',
            self.create_bong,
        )
        self._server.listen(
            '^count$',
            self.count,
        )

        members = await get_emails(spark, self._bongs['room'])

        msg = '''{}
<br/>
<br/>
To get something to drink, write **bong** to me and show the QR code in the bar!
<br/>
<br/>
**Do not scan the QR code yourself.**
<br/>
It is a one time code, and you will not get a drink for it after it has been used!
'''.format(self._bongs['welcome_message'])

        await self._notify_all(members, msg, spark)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            spark.messages.create,
            None,
            message.personId,
            None,
            'Done sending notifications')

    async def create_bong(self, loop, spark, message):
        if self._all_bongs_created(message.personId):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                spark.messages.create,
                None,
                message.personId,
                None,
                'You have already received all your bongs')
            return

        self._people[message.personId] = self._people.get(message.personId, 0) + 1
        await self._send_new_bong(spark, message.personId)

    async def count(self, loop, spark, message):
        if not self._allowed(message.personEmail):
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            spark.messages.create,
            None,
            message.personId,
            None,
            'There have been a total of {} bongs validated'.format(
                len(self._validated)
            )
        )

    async def validate(self, spark, info):
        entry = info.get('entry', None)
        if not entry or not self._valid_bongs.get(entry, None):
            text = validate_html.format(text='Invalid QR code', color='red')
            return text, 404

        personId = self._valid_bongs[entry]
        del self._valid_bongs[entry]
        self._validated.append(entry)

        if not self._all_bongs_created(personId):
            await self._send_new_bong(spark, personId)

        text = validate_html.format(
            text='QR code is valid for one drink!',
            color='green'
        )
        return text, 200

    async def draw(self, loop, spark, message):
        if not self._allowed(message.personEmail):
            return

        completers = await self._get_completers(spark)

        if not completers:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                spark.messages.create,
                None,
                message.personId,
                None,
                'No non-excluded people have completed the challenge.')
            return

        winner = random.choice(completers)
        await self._notify_winner(winner, spark, message.personId)

    def _all_bongs_created(self, personId):
        limit = self._bongs.get('limit', None)
        return limit and self._people.get(personId, 0) >= self._bongs['limit']

    async def _send_new_bong(self, spark, personId):
        self._people[personId] = self._people.get(personId, 0) + 1
        bong_id, img = self._create_new_bong()
        await self._send_bong(img, bong_id, personId, spark)

    async def _send_bong(self, image, bong_id, personId, spark):
        loop = asyncio.get_event_loop()
        with tempfile.NamedTemporaryFile(suffix='.png') as fd:
            image.save(fd)

            try:
                await loop.run_in_executor(
                    None,
                    spark.messages.create,
                    None,
                    personId,
                    None,
                    'Here is your new bong, show this to the bartender when you want a new drink',
                    None,
                    [fd.name])
                self._valid_bongs[bong_id] = personId
            except ciscosparkapi.exceptions.SparkApiError:
                self._people[personId] -= 1
                await loop.run_in_executor(
                    None,
                    spark.messages.create,
                    None,
                    personId,
                    None,
                    'I\'m sorry, something went wrong when trying to send the bong to spark. Please try again')

    def _create_new_bong(self):
        bong_id = str(uuid.uuid4())

        qr = qrcode.QRCode(border=0)
        qr.add_data('{}/{}'.format(self._validate_url, bong_id))
        qr.make()
        if self._background:
            mask = qr.make_image()
            mask = mask.resize(self._background.size)
            img = PIL.Image.composite(self._background, self._foreground, mask)
        else:
            img = qr.make_image()
        return bong_id, img

    async def _get_completers(self, spark):
        rooms = self._draw.get('rooms', [])

        first_room = rooms[0]
        rooms = rooms[1:]
        possible = set(await get_emails(spark, first_room))

        for room in rooms:
            needed = set(await get_emails(spark, room))
            possible = possible.intersection(needed)

        return [email for email in possible if not self._should_exclude(email)]

    async def _notify_winner(self, winner, spark, personId):
        loop = asyncio.get_event_loop()
        people = await loop.run_in_executor(
            None,
            spark.people.list,
            winner)

        for p in people:
            winner_message = '''Congratulations {}! You won'''.format(p.displayName)
            response = 'The winner is {} ({})'.format(p.displayName, p.emails[0])

            await loop.run_in_executor(
                None,
                spark.messages.create,
                None,
                personId,
                None,
                response)
            await loop.run_in_executor(
                None,
                spark.messages.create,
                None,
                None,
                p.emails[0],
                winner_message)
            return

    async def _notify_all(self, members, message, spark):
        loop = asyncio.get_event_loop()
        for email in members:
            if self._should_ignore(email):
                continue

            await loop.run_in_executor(
                None,
                spark.messages.create,
                None,
                None,
                email,
                None,
                message)

    def _allowed(self, email):
        for admin in self._admins:
            if re.match(admin, email):
                return True
        return False

    def _should_ignore(self, email):
        for ignore in self._ignore:
            if re.match(ignore, email):
                return True
        return False

    def _should_exclude(self, email):
        for exclude in self._draw.get('exclude', []):
            if re.match(exclude, email):
                return True
        return False

    def _setup_server(self, config):
        loop = asyncio.get_event_loop()
        self._server = Server(
            config['bot'],
            loop
        )

        self._server.listen(
            '^party!$',
            self.party,
        )
        self._server.add_get('/validate/{entry}', self.validate)

        if self._draw:
            self._server.listen('^draw$', self.draw)

        loop.run_until_complete(self._server.setup())

    def run(self):
        loop = asyncio.get_event_loop()
        print('======== Bot Ready ========')
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        except:
            print(sys.exc_info())
        finally:
            loop.run_until_complete(self._server.cleanup())
