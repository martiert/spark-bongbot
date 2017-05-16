import json
import signal
import subprocess
import time
import sys
import threading
import tempfile
import asyncio
import aiohttp

from spark import Server


class State:
    def __init__(self, config):
        self._config = config

    def done(self):
        return False


class Done(State):
    def __init__(self, config):
        super(Done, self).__init__(config)

    def done(self):
        return True


class Ignore(State):
    def __init__(self, config):
        super(Ignore, self).__init__(config)

    def ask_question(self):
        return '''Are there any emails you want ignored?

If so, add them by writing a comma-separated list of emails (e.g. 'first@gmail.com,second@domain.com')
If you don't want to ignore anyone, write 'None'
'''

    def answer(self, ans):
        if not ans.lower() == 'none':
            ignore = self._config.get('ignore', [])
            ignore.extend(email.strip() for email in ans.split(','))
            self._config['ignore'] = ignore
        return Done(self._config), None


class Administrators(State):
    def __init__(self, config):
        super(Administrators, self).__init__(config)

    def ask_question(self):
        return '''Currently, only you are administrator for this instance.

Add more by writing a comma-separated list of emails (e.g. 'first@gmail.com,second@domain.com')
If you want to be the only administrator, write 'None'
'''

    def answer(self, ans):
        if not ans.lower() == 'none':
            emails = [email.strip() for email in ans.split(',')]
            self._config['administrators'].extend(emails)
        return Ignore(self._config), None


class Welcome(State):
    def __init__(self, config):
        super(Welcome, self).__init__(config)

    def ask_question(self):
        return 'What do you want as your welcome message?'

    def answer(self, ans):
        self._config['bongs']['welcome_message'] = ans
        return Administrators(self._config), None


class Limit(State):
    def __init__(self, config):
        super(Limit, self).__init__(config)

    def ask_question(self):
        return 'How many bongs will you allow per person? (0 means unlimited bongs)'

    def answer(self, ans):
        if not ans.isdigit():
            return self, '\'{}\' is not a digit'.format(ans)
        number = int(ans)
        if not number == 0:
            self._config['bongs']['limit'] = number
        return Welcome(self._config), None


def create_child_bots(bot_ids, port, basehook, base_config):
    import copy
    child_bots = {}
    for bot in bot_ids:
        port += 1
        config = copy.deepcopy(base_config)
        config['bot'] = {
            'token': bot['token'],
            'webhook': '{}/{}'.format(basehook, port),
            'port': port,
        }
        child_bots[bot['token']] = {
            'in_use': False,
            'port': port,
            'token': bot['token'],
            'email': bot['email'],
            'config': config,
        }
    return child_bots


class Admin:
    def __init__(self, config):
        self._max_duration = config.get('max-duration', None)
        self._baseconfig = config.get('baseconfig', {})

        base_hook = config['bot']['webhook']
        initial_port = config['bot']['port']

        self._children = create_child_bots(
            config['children'],
            initial_port,
            base_hook,
            self._baseconfig,
        )

        self._states = {}
        self._setup_server(config)

    async def proxy_post(self, api, request):
        child = request.match_info.get('child', None)
        if not child:
            return 404

        if not child.isdigit():
            return 404

        data = await request.json()
        async with aiohttp.ClientSession() as session:
            async with session.post('http://127.0.0.1:{}'.format(child), data=json.dumps(data)) as resp:
                return resp.status

    async def proxy_get(self, api, request):
        child = request.match_info.get('child', None)
        entry = request.match_info.get('entry', None)
        if not child:
            return '', 404

        if not child.isdigit():
            return '', 404,

        async with aiohttp.ClientSession() as session:
            async with session.get('http://127.0.0.1:{}/validate/{}'.format(child, entry)) as resp:
                text = await resp.text()
                return text, resp.status

    async def created(self, api, roomid, membership_id, person):
        loop = asyncio.get_event_loop()

        if person.id in self._states:
            await loop.run_in_executor(
                None,
                api.messages.create,
                None,
                person.id,
                None,
                'Please finish your current instance creation before trying to create a new instance')
            return

        child = self._reserve_child()
        if not child:
            await loop.run_in_executor(
                None,
                api.messages.create,
                None,
                person.id,
                None,
                'Sorry! I have no more capacity at this point. You can host your own instance by using https://github.com/martiert/spark-bongbot')
            return

        bongs = child['config'].get('bongs', {})
        bongs['room'] = roomid
        child['config']['administrators'] = person.emails
        child['membership'] = membership_id
        child['owner'] = person.emails[0]
        self._states[person.id] = {
            'config': child,
            'state': Limit(child['config'])
        }
        question = self._states[person.id]['state'].ask_question()
        await loop.run_in_executor(
            None,
            api.messages.create,
            None,
            person.id,
            None,
            question)

    def wait_for_timeout(self, child, token, api, subbot_id, parent_id):
        time.sleep(self._max_duration * 3600)
        child.send_signal(signal.SIGINT)
        self._children[token]['in_use'] = False

        api.memberships.delete(subbot_id)
        api.memberships.delete(parent_id)

    async def answer(self, api, message):
        loop = asyncio.get_event_loop()

        if message.personId not in self._states:
            return

        next_state, error = self._states[message.personId]['state'].answer(message.text)
        if next_state.done():
            config = self._states[message.personId]['config']
            child = self._create_child(config)
            membership = await loop.run_in_executor(
                None,
                api.memberships.create,
                config['config']['bongs']['room'],
                None,
                config['email'])
            await loop.run_in_executor(
                None,
                api.messages.create,
                None,
                message.personId,
                None,
                'Your instance is created. It will be automatically deleted in {} hours'.format(self._max_duration))

            token = config['token']
            membership_id = config['membership']

            del self._states[message.personId]
            t = threading.Thread(
                None,
                self.wait_for_timeout,
                "wait thread",
                (child, token, api, membership.id, membership_id))
            t.run()
            t.daemon = True
            return

        if error:
            await loop.run_in_executor(
                None,
                api.messages.create,
                None,
                message.personId,
                None,
                error)

        question = next_state.ask_question()
        self._states[message.personId]['state'] = next_state

        await loop.run_in_executor(
            None,
            api.messages.create,
            None,
            message.personId,
            None,
            question)

    def _reserve_child(self):
        for token, child in self._children.items():
            if not child['in_use']:
                self._children[token]['in_use'] = True
                return child
        return None

    def _setup_server(self, config):
        loop = asyncio.get_event_loop()
        self._server = Server(
            config['bot'],
            loop
        )
        self._server.roomcreation(self.created)
        self._server.default_message(self.answer)
        self._server.add_post('/{child}', self.proxy_post)
        self._server.add_get('/{child}/validate/{entry}', self.proxy_get)

        loop.run_until_complete(self._server.setup())

    def _create_child(self, config):
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as fp:
            json.dump(config['config'], fp)
            name = fp.name

        child = subprocess.Popen([
            'python',
            '-m', 'bongbot',
            '--cleanup',
            '--config', name,
            '--owner', config['owner']
        ])
        return child

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
