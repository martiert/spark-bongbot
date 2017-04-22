BongBot
=======

BongBot handles creating and validating drink bongs for events. The bot uses Cisco Spark as a backend.

Setup
-----

I recomend running the bot in a virtualenv. After creating the virtualenv, you can install all dependencies by
running:

pip install -r requirements.txt

You can then start the bot by writing 'python -m bongbot --config <config file>'

Configuration file
------------------

The configuration file is a simple json document. An example configuration file can be seen in
example.json

**bot section**

- token: The Spark token to use for this bot
- webhook: The URL the bot is listening for messages from Spark on
- port: The port the bot is listening to

The bot only listenes to localhost:<port>, see the HTTP Server setup for what is required.

This section is Required!

**bongs section**

- room: The roomid the bot sends its greeting to
- welcome_message: The welcome message to send to the people in the room
- background: The background to be used for the QR code
- limit: Max number of QR codes to generate for one person

background and limit are optional. If limit is missing, we will generate pure black/white QR codes.
If limit is missing, we will allow an unlimited amount of QR codes to be generated.

This section is Required!

**administrators**

A list of regexes to identify administrators by email addresses. Administrators are the ones allowed
to start the party, by calling 'party!', count the number of bongs validated by calling 'count', and
do the drawing of a winner by calling 'draw'.

If this list is missing, everyone is assumed to be an administrator.

**ignore**

A list of regexes to identify accounts that should not be greeted, by email addresses.

If this list is missing, everyone is greeted.

**draw**

- rooms: The roomids to use for the drawing
- exclude: A list of regexes to identify email addresses to exclude from the competition.

The drawing is done by drawing a random person that is in all of the rooms given.

If exclude is missing, noone is excluded from the drawing. If the whole section is missing the 'draw' command
is missing.

HTTP server setup
-----------------

The bot only listenes to localhost, so you are dependent on a webserver, like nginx, to proxy requests to the bot.

You need to make sure your server sends requests to your webhook url to localhost:<configured port>, as well as
requests to <configure webhook>/validate.

If you're using nginx, the bot is listening to port 3000, and the webhook url is 'https://my_webhook_domain.com/spark',
the following location entry will work:

    location /spark {
        rewrite /spark/?(.*) /$1 break;
        proxy_pass http://localhost:3000;
    }
