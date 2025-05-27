# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""This is the basic stream protocol for ESP.

Example stream reader:
    # This would register a consumer group for github discussions and handle payloads
    # waiting in the processing pipeline, then push them to the pubsub queue.
    input_stream = esp.streams.Stream(esp.streams.Pipelines.INBOUND)
    pubsub_stream = esp.streams.Stream(esp.streams.Pipelines.PUBLISHING)
    my_processing_group = input_stream.group("github_dicsussions")
    for payload in my_processing_group:
        foo = process_payload(payload.data)  # Process the payload
        pubsub_stream.push(foo)  # Push the resulting pubsub dict (or whatever) to the pubsub queue
        payload.complete()  # Marks this payload as successfully handled, removing it from the queue.

    """

import typing
import asfpy.whoami
import valkey.asyncio
import time
import msgpack
import os

WHOAMI = asfpy.whoami.whoami()
BLOCK_INTERVAL = 5000  # Block for 5000 ms when reading stream queues
DEFAULT_GROUP = "default"  # If not otherwise specified, we group reads into this default consumer group

SEEK_BEGINNING = "0-0"  # Seek cursor for valkey group read()s. 0-0 means "Go through any items on our PEL"
SEEK_POLL = ">"  # Seek cursor for valkey group read()s. > means "Poll for any incoming events in this stream"
VALKEY_HOSTFILE = "/var/app/host.txt"
VALKEY_HOST = open(VALKEY_HOSTFILE).read().trim() if os.path.isfile(VALKEY_HOSTFILE) else "localhost"

class Pipelines:
    """These are the basic three pipelines for processing plus feedback loop"""

    INGRES = "ingress_raw"  # Raw input from 3rd parties, just store and keep trucking. auth processors pick these up
    # and move them to ingress_verified once verified.
    INBOUND = "ingress_verified"  # This is the inbound stream of legit events, where the original payloads are transformed into a format we can better utilize for pubsub etc.
    PUBLISHING = "pubsub"  # The stream where payloads are queued up for publishing to pypubsub
    FEEDBACK = "pubsub_feedback"  # This is where external agents can register feedback on pubsub events
    NOT_SET = object()

# our valkey store is only accessible through our beanstalk security group, so we can relax on credentials for now...
_vk = valkey.asyncio.Valkey(decode_responses=False, host=VALKEY_HOST, username="default", password="default")


class Agent:
    def __init__(self, role: str, agent_id: str = WHOAMI):
        self.role = role
        self.agent_id = agent_id

    def read(self, stream: str, blocking: bool = True) -> typing.AsyncIterator:
        """Begins reading from a stream. In the event that there are older entries in the PEL,
        these entries will be fetched first, and then polling for new entries will begin.
        If `blocking` is set to False, the read call will return immediately, whether or not
        an entry was waiting in the queue."""
        local_stream = Stream(stream)
        return local_stream.read(client_group=self.role, client_id=self.agent_id, blocking=blocking)

    async def write(self, stream: typing.Union[str, "Stream"], data: "Stream.Entry") -> str:
        """Writes a stream entry to a stream. Returns the EID of the saved entry in the stream."""
        if isinstance(stream, str):  # We accept an open stream or just the name of a stream
            stream = Stream(stream)
        return await stream.write(data, self.agent_id)

    def stream_info(self, stream:typing.Union[str, "Stream"]):
        if not isinstance(stream, str):  # We accept an open stream or just the name of a stream
            stream = stream.name
        return _vk.xinfo_stream(stream)

    def role_info(self, stream:typing.Union[str, "Stream"]):
        if not isinstance(stream, str):  # We accept an open stream or just the name of a stream
            stream = stream.name
        return _vk.xinfo_groups(stream)



class Stream:
    def __init__(self, name: str = Pipelines.INBOUND):
        """Instantiates an event stream for both sending and receiving events."""
        self.name: str = name
        self._initialized_groups: list = []

    async def write(self, entry: "Entry", client_id: str = None):
        """Writes a payload object to the event stream"""
        # We msgpack it here as we may not know the format of this payload, so to ensure it is
        # something we can store in the stream, we pack it up as a binary blob and append
        # metadata variables for tracking. We pack everything up as that allows us to skip
        # encoding in valkey altogether, which prevents breakage with binary data in the dicts.
        data_packed = msgpack.packb(
            {
                "ts": time.time_ns() / 1000000000.0,
                "client_id": client_id or WHOAMI,
                "client_originator": WHOAMI,  # Even if client_id is tailored, we like to keep a track of the machine itself
                "initiator": entry.initiator,
                "data": entry.data,
                "headers": entry.headers,
            }
        )
        eid = await _vk.xadd(
            name=self.name,
            fields={"data": data_packed},
        )
        if isinstance(eid, bytes):
            eid = eid.decode("us-ascii")
        return eid

    async def _init_group(self, group_name: str):
        try:
            await _vk.xgroup_create(self.name, group_name, mkstream=True)
        except valkey.exceptions.ResponseError:
            pass  # Already exists, all is well
        self._initialized_groups.append(group_name)

    class Entry:
        def __init__(
            self,
            data: typing.Optional[typing.Any] = None,  # if supplied, the Entry object is populated with this stream entry data
            headers: typing.Optional[dict] = None,     # If this entry originated from a HTTP request, put the headers here
            initiator: typing.Union["Entry", str, None] = None,  # backlink to whoever initiated this event
            stream: typing.Optional["Stream"] = None,
            eid: typing.Optional[str] = None,
            client_group: typing.Optional[str] = None,
        ):
            self.eid = eid
            self.headers = headers or {}
            if data:
                self.ts = data.get("ts", 0)
                self.client_id = data.get("client_id")
                self.data = data.get("data")
                self.initiator = data.get("initiator", "none")
            else:
                self.ts = time.time_ns() / 1000000000.0
                self.client_id = WHOAMI
                self.initiator = "none"

            self.stream = stream or Pipelines.NOT_SET

            if initiator:
                if isinstance(initiator, self.__class__):
                    self.initiator = f"pipeline::{initiator.stream.name}::{initiator.eid}"
                else:
                    self.initiator = f"external::{initiator}"

            self.client_group = client_group

        async def complete(self):
            """Marks an event as fully processed by this consumer group, removing it from the pending Entries List (PEL)"""
            rv = await _vk.xack(self.stream.name, self.client_group, self.eid)
            return rv

        def response(self) -> "Entry":
            """Generates an empty response-entry to this stream entry, with its origin mapped to this entry."""
            return self.__class__(data=None, initiator=self)

    async def read(
        self, client_group: str = DEFAULT_GROUP, client_id: str = WHOAMI, blocking=True
    ) -> typing.AsyncIterator[Entry]:
        """Reads from an event stream. If `client_group` is specified, a consumer group is created,
        wherein all incoming events are distributed among the members of that consumer group,
        so that no listener in that group will get the same message another listener has already
        received for processing. If you have multiple consumer groups, each group will receive a
        copy of the event sent to this stream, and will then distribute that copy to a single
        agent inside the group. The `client_id` argument is used to differentiate the
        various agents connected to this consumer group, as well as for historical retrieval
        of events that were received by this agent but not fully processed.

        Each call to read() will return at most one entry from the stream queue, or None if there
        are no items pending. If `blocking` is set to True (default), read will block until a new
        event entry appears in the stream, otherwise it will return None immediately if there
        are no pending items in the stream."""
        if client_group not in self._initialized_groups:
            await self._init_group(client_group)
        cursor = SEEK_BEGINNING
        while True:
            try:
                rv = await _vk.xreadgroup(
                    groupname=client_group,
                    consumername=client_id,
                    streams={self.name: cursor},
                    count=1,
                    block=BLOCK_INTERVAL if blocking else None,
                )
                if rv and rv[0][1]:
                    eid, data = rv[0][1][0]
                    # Unpack the msgpacked data
                    data = msgpack.unpackb(data.get(b"data"))
                    if "data" in data:
                        yield Stream.Entry(
                            data=data, headers=data.get("headers"), stream=self, eid=eid.decode("us-ascii"), client_group=client_group
                        )
                        if not blocking:
                            break
                else:
                    # If we received no entries, and the cursor is set to SEEK_BEGINNING ("0-0"), that means we have reached the end of
                    # any PEL history we had for this client. We then change the cursor to SEEK_POLL (">"), meaning any new events that
                    # have come in or will come in on this stream channel. The PEL consists of any messages relayed to
                    # this client but not fully handled and finalized with .complete(). This allows us to pick up where
                    # we left off, whether due to the process crashing, networking issues, etc.
                    if cursor == SEEK_BEGINNING:
                        cursor = SEEK_POLL
                        continue
                    if not blocking:
                        break
            except valkey.exceptions.TimeoutError as e:
                if not blocking:  # If not due to blank queue in blocking mode, pass on the failure.
                    raise e
