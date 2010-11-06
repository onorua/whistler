#! /usr/bin/env python
# -*- encoding: utf-8 -*-
# vim:fenc=utf-8:
#
# This code is heavly based on quinoa, which is:
#   (c) 2010 Kit La Touche

"""
The bot module
--------------

The bot module provide a set of classes to instance a basic bot, which
handle commands received in MUC stream and also user messages to, and
parse them trying to execute a valid command.

The :class:`WhistlerBot` is the main class used to start the bot, and is
designed to be extended when require. Let's an example:

.. code-block:: python

    from whistler.bot import WhistlerBot

    class MyBot(WhistlerBot):
        def cmd_ping(self, msg, args):
            return "pong"


The previous example create a ping/pong bot in a three lines. More complex
action can be used too.
"""

import os
import sys
import time
import random
import warnings

warnings.filterwarnings("ignore",category=DeprecationWarning)
import xmpp

from whistler.log import WhistlerLog
from whistler.job import WhistlerIdleJob, WhistlerWorkJob

xmpp.NS_CONFERENCE = "jabber:x:conference"

COMMAND_CHAR = "!"

def restricted(fun):
    """ The restricted decorator is designed to work in commands, to check
    if user is in authorized users to perform an action. Example of usage:

    .. code-block:: python

      @restricted
      def cmd_ping(self, msg, args):
          return "pong"

    In this example ping command is only allowed to authenticated users. """

    def new(self, msg, args):
        user = "%s@%s" % (msg.getFrom().getNode(), msg.getFrom().getDomain())
        if self.is_validuser(user):
            return fun(self, msg, args)
        else:
            self.log.warning("ignoring command %s, invalid user %s." % \
                            ( fun.__name__[4:], user ))
    return new


class WhistlerConnectionError(Exception):
    """ A exception which will be raised on bot connection error. """


class WhistlerBot(object):
    """ The main WhistlerBot class handle the bot behaviour and
    perform subcall to specific command handler when a command
    is received in a configured MUC channel. """


    def __init__(self, jid, password, server=None, rooms=None,
            resource=None, log=None, users=None):
        """ Create a new :class:`WhistlerBot` object, the :func:`__init__`
        receive the following parameters:

        :param `jid`: a valid JID atom to identify the bot user.
        :param `password`: a plaintext password to identify the bot user.
        :param `server`: a tuple in the form *server*, *port* which sepcify
            the host to connect to, if not provided the server then use the
            JID domain instead.
        :param `rooms`: a :class:`list` of rooms to be autojoin in.
        :param `resource`: the XMPP resource string, or autogenerated one if
            not provided.
        :param `log`: a :class:`WhistlerLog` to log bot messages to, or
            *stdout* if none is provided.
        :param `users`: a :class:`set` of valid JID as strings which
            identify master users.
        """

        self.jid = xmpp.JID(jid)
        self.password = password
        self.server = server or ( self.jid.getDomain(), 5222 )
        self.log = log or WhistlerLog()
        self.debug = False
        self._initial_users = users

        self.idle = None
        self.client = None
        self.rooms = {}

        for room in rooms or []:
            self.rooms[room] = resource

        self.resource = resource or self.__class__.__name__.lower() + \
                                    str(random.getrandbits(32))


    @property
    def users(self):
        """ A property which return an iterator over users in bot roster, that is
        administrative users or valid users to admin the bot. """

        roster = self.client.getRoster()

        for jid in roster.getItems():
            if jid not in self.rooms and jid != self.jid:
                yield jid


    def on_connect(self):
        """ This function can be override to handle the connection event.
        When bot is sucessfully connect, the actions defined in this
        function will be executed. """


    def on_disconnect(self):
        """ This function can be override to handle the disconnection event.
        Before bot is sucessfully disconnect, the actions defined in this
        function will be executed. """


    def send_to(self, who, data):
        """ Send a chat message to any user. This function is designed to
        be called from user custom handle functions, like :func:`on_connect`
        or :func:`on_register_user`.

        :param `who`: The JID as string representation of the recipient.
        :param `data`: An string which contain the message to be set. """
        dest = xmpp.JID(who)
        self.client.send( xmpp.protocol.Message(dest, data, "chat") )


    def set_subject(self, room, subject):
        """ Set a new subject on specified room. """

        if room in self.rooms.keys():
            dest = xmpp.JID(room)
            mesg = "Whistler set subject to: %s" % subject
            self.client.send( xmpp.protocol.Message(dest, mesg,
                              "groupchat", subject=subject) )


    def connect(self):
        """ Perform a connection to the server, this function is designed to
        work internally, but calls to :func:`on_connect` when connection is
        sucessfully. """

        if self.client:
            return self.client

        debug = debug=['always', 'nodebuilder'] if self.debug else []

        self.client = xmpp.client.Client(self.jid.getDomain(), debug=debug)

        if not self.client.connect(server=self.server, secure=True):
            raise WhistlerConnectionError(
                "unable to connect to %s using port %d" % self.server
            )
        else:
            self.log.info("connected to %s, port %d" % self.server)


        if not self.client.auth(self.jid.getNode(), self.password, self.resource):
            raise WhistlerConnectionError(
                "unable to authorize user %s" % self.jid.getNode()
            )
        else:
            self.log.info("authorized user %s" % self.jid.getNode())


        self.client.RegisterHandler("message",  self.handle_message)
        self.client.RegisterHandler("presence", self.handle_presence)
        self.client.UnregisterDisconnectHandler(self.client.DisconnectHandler)
        self.client.RegisterDisconnectHandler(self.on_disconnect)

        self.client.sendInitPresence()

        self.on_connect()

        self.join(self.rooms.keys())

        self.idle = WhistlerIdleJob(self.client, 60)

        for user in self._initial_users:
            self.register_user(user)

        return self.client


    def on_register_user(self, who):
        """ This function can be override to handle the registration event.
        When bot is successfully subscribed to any admin user, the actions
        defined in this function will be executed.

        :param `who`: the JID as string representation of the user which
            is recenlty added.
        """


    def register_command(self, cmdname, cmdfun):
        """ Register on the fly a new command. This function intend to
        provide a way to add commands on-the-fly, when :class:`WhistlerBot`
        is alreay instanced.

        :param `cmdname`: a name to this command.
        :param `cmdfun`: a callback which can accept three arguments, which
            will be usd when command called. """

        setattr(self, "cmd_%s" % cmdname, cmdfun)


    def start(self):
        """ Start to serve the bot, until finished signal is received, using
        for that the :func:`stop`. """

        if not self.connect():
            raise WhistlerConnectionError("unknown error")

        self.idle.start()

        while self.client.isConnected():
            self.client.Process(10)


    def stop(self):
        """ Stop the bot to serve, this function also destroy current
        connection if exists. """

        self.disconnect()

        if self.idle:
            self.idle.stop()


    def is_validuser(self, jid):
        """ Return if the specified user is registered as valid user in the
        bot, according to :func:`register_user` and :func:`unregister_user`
        functions. """

        if jid in self.rooms:
            return False

        roster = self.client.getRoster()

        if jid in roster.getItems():
            return True
        else:
            return False


    def register_user(self, jid):
        """ Register an user as valid user for the bot. """

        roster = self.client.getRoster()
        roster.Subscribe(jid)
        roster.Authorize(jid)
        self.client.send(xmpp.protocol.Presence(to=jid, typ="subscribe"))


    def unregister_user(self, jid):
        """ Unregister an user as valid user for the bot. """

        if jid not in self.rooms and jid != self.jid:
            roster = self.client.getRoster()
            roster.Unsubscribe(jid)
            roster.Unauthorize(jid)
            roster.delItem(jid)


    def handle_presence(self, client, message):
        """ Handle the presence in XMPP server, this function is designed to
        work internally to bot, and handle the presence subscription
        XMPP message. """

        presence_type = message.getType()
        who = message.getFrom()

        if presence_type == "subscribe":

            if who not in self._initial_users:
                return

            self.client.send(xmpp.protocol.Presence(to=who, typ="subscribed"))
            self.client.send(xmpp.protocol.Presence(to=who, typ="subscribe"))

        if presence_type == "subscribed":
            self._initial_users.discard(who)
            self.on_register_user(who)


    def handle_message(self, client, message):
        """ Handle any received message from the XMPP server, this function
        is designed to work internally, and performs subcalls to any command
        function defined in the object when the properly command is
        received. """

        for node in message.getChildren():

            if node.getAttr("xmlns") == xmpp.NS_MUC_USER or \
               node.getNamespace() == xmpp.NS_CONFERENCE:

                   room = msg.getFrom().getNode()
                   serv = msg.getFrom().getDomain()

                   # Begin the join iteration process
                   self._joining = self.join_room(room, serv)
                   self._joining.next()
                   return


        if message.getType() == "groupchat":
            _room = message.getFrom()
            room  = "%s@%s" % ( _room.getNode(), _room.getDomain() )

            if room in self.rooms.keys() and \
               self.rooms[room] == _room.getResource():
                   return

        body = message.getBody()

        if not body or (body[0] != COMMAND_CHAR \
                and not body.startswith(self.resource + ", ") \
                and not body.startswith(self.resource + ": ")):
            # None to handle
            return

        if body[0] == COMMAND_CHAR:
            command_n = body.split()[0][1:]
            arguments = body.split()[1:]
        else:
            command_n = body.split()[1]
            arguments = body.split()[2:]

        command = getattr(self, "cmd_%s" % command_n, None)

        if command:
            self.log.info("received command %s with arguments %s" % \
                         ( command_n, str(arguments) ))
            self.send(message, command, arguments)


    def handle_error(self, client, message):
        """ Handle error when register presence on groupchat, this function
        provide a way to rejoin on some kind of errors. """

        try:
            if message.getType == "error" and msg.getErrorCode() == "409":
                self._joining.send(False)
            else:
                self._joining.send(True)
        except StopIteration:
            pass


    def join(self, rooms):
        """ Join into rooms specified in argument, as a :class:`list` of
        strings which contain valid room names (*name*@*server*). """

        for room in rooms:
            # Begin the join iteration process
            try:
                room, serv = room.split('@')
            except ValueError:
                self.log.warning("invalid room ot join: %s" % room)
                continue

            self._joining = self.join_room(room, serv)
            self.log.info("joined to %s@%s" % ( room, serv ))
            self._joining.next()


    def join_room(self, room, server, resource=None):
        """ Perform a bot join into a MUC room, aditional resource name can
        be provided to identify the bot in the MUC.

        :param `room`: The room name (whitout server statement).
        :param `server`: The conference server where room lives.
        :param `resource`: A resource name for the bot in the room.  """

        self.client.RegisterHandler("presence", self.handle_error)
        resource = resource or self.resource or "whistler"

        while True:
            room_presence = xmpp.protocol.JID(node = room, domain = server,
                    resource = resource)
            self.client.send(xmpp.protocol.Presence(room_presence))
            self.rooms[u"%s@%s" % ( room, server ) ] = resource

            no_error = (yield)

            if no_error:
                break

            resource += "_"
            self.log.warnings("invalid resource name from room %s, " % room +
                              "trying new one (%s)" % resource)

        self.client.RegisterHandler("presence", self.handle_presence)


    def leave(self, rooms):
        """ Leave the rooms specified in argument, as a :class:`list` of
        strings which contain valid room names (*name*@*server*). """

        for room in rooms:
            try:
                room, server = room.split('@')
            except ValueError:
                self.log.warning("invalid room to leave: %s" % room)
                continue

            self.log.info("leaving room: %s@%s" % (room, server))
            self.leave_room(room, server)


    def disconnect(self):
        """ Leave the server, setting the bot presence to unavailable
        and close server connections. """

        self.client.UnregisterHandler("message", self.handle_message)
        self.client.UnregisterHandler("presence", self.handle_presence)
        self.log.info("Shutting down the bot...")
        self.client.disconnected()


    def leave_room(self, room, server, resource=None):
        """ Perform an action to leave a room where currently the bot is in.

        :param `room`: the room name to leave.
        :param `server`: the server where room is.
        :param `resource`: the resource which leaves. """

        room_id = "%s@%s" % ( room, server)
        room_presence = xmpp.protocol.JID(node = room, domain=server,
                    resource = resource or self.rooms[room_id])

        self.client.send(xmpp.protocol.Presence(to=room_presence,
                                                typ="unavailable"))
        self.rooms.pop(room_id)


    def send(self, message, command, arguments=[]):
        """ Send a XMPP message contains the result of command execution
        with arguments passed. The original message is also provided to
        known who sent the command.

        :param `message`: The original :class:`xmpp.protocol.Message`
        :param `command`: The command handled.
        :param `arguments`: a :class:`list` of arguments to the command. """

        dest = message.getFrom()

        if message.getType() == "groupchat":
            dest.setResource("")

        reply = command(message, arguments)

        self.client.send(xmpp.protocol.Message(dest, reply, message.getType()))


if __name__ == "__main__":
    class TestBot(WhistlerBot):
        def cmd_echo(self, msg, args):
            text = msg.getBody()
            return text

        def cmd_list_rooms(self, msg, args):
            return ', '.join(self.rooms.keys())

        def cmd_whoami(self, msg, args):
            return "You are %s" % msg.getFrom()

    try:
        b = TestBot('test@connectical.com',  'password',
                server = ("talk.google.com", 5223), resource = 'Bot')
        b.start()

    except KeyboardInterrupt:
        pass
    finally:
        b.stop()

