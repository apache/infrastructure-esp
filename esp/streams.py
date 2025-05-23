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

WHOAMI = asfpy.whoami.whoami()
BLOCK_INTERVAL = 5000  # Block for 5000 ms when reading stream queues
DEFAULT_GROUP = "default"  # If not otherwise specified, we group reads into this default consumer group

SEEK_BEGINNING = "0-0"  # Cursor for read() for starting from the beginning of our PEL
SEEK_POLL = ">"  # Cursor for read() for starting at any incoming stream event (poll)


class Pipelines:
    """These are the basic four pipelines for processing plus feedback loop"""

    INGRES = "ingress_raw"  # Raw input from 3rd parties, just store and keep trucking. auth processors pick these up
    VERIFICATION = "ingress_verify"  # Verification processors verify whether an event is legit and legible, and if so, forwards it to the inbound stream
    INBOUND = (
        "ingress_verified"  # This is the inbound stream of legit events, where the original payloads are transformed into a format we can better utilize for pubsub etc.
    )
    PUBLISHING = "pubsub"  # The stream where payloads are queued up for publishing to pypubsub
    FEEDBACK = "pubsub_feedback"  # This is where external agents can register feedback on pubsub events


_vk = valkey.asyncio.Valkey(decode_responses=True)


class Stream:
    def __init__(self, name: str = Pipelines.INBOUND):
        """Instantiates an event stream for both sending and receiving events."""
        self.name: str = name
        self.group = lambda group_name: self.read(group_name)
        self._initialized_groups: list = []

    async def push(self, payload: dict):
        eid = await _vk.xadd(
            name=self.name,
            fields=payload,
        )
        print(eid)

    async def _init_group(self, group_name: str):
        try:
            await _vk.xgroup_create(self.name, group_name, mkstream=True)
        except valkey.exceptions.ResponseError:
            pass  # Already exists, all is well
        self._initialized_groups.append(group_name)

    class StreamEvent:
        def __init__(
            self, parent: "Stream", client_group: str, client_id: str, eid: str, data: typing.Union[typing.Dict, None]
        ):
            self.stream = parent
            self.client_group = client_group
            self.eid = eid
            self.data = data

        async def complete(self):
            """Marks an event as fully processed by this consumer group, removing it from the pending Entries List (PEL)"""
            rv = await _vk.xack(self.stream.name, self.client_group, self.eid)
            return rv

    async def read(
        self, client_group: str = DEFAULT_GROUP, client_id: str = WHOAMI, blocking=True
    ) -> typing.AsyncIterator[StreamEvent]:
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
                    yield Stream.StreamEvent(
                        parent=self, client_group=client_group, client_id=client_id, eid=eid, data=data
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
