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

import asfquart
import esp.streams
import valkey
import time
import ago

app = asfquart.APP

target_stream = esp.streams.Pipelines.INGRES
me = esp.streams.Agent("ingress")


@app.route("/status")
async def store_status():
    status_dict = {}
    for stream in esp.streams.pipes:
        try:
            stream_info = await me.stream_info(stream)
            last_eid = stream_info.get("last-generated-id", b"0-0").decode("us-ascii")
            last_event = 0
            if "-" in last_eid:
                last_event = int(last_eid.split("-")[0])/1000.0
            status_dict[stream] = {
                "num_entries": stream_info.get("length"),
                "total_entries": stream_info.get("entries-added"),
                "last_event_ts": time.ctime(last_event),
                "last_event_text": ago.human(last_event),
                "last_document": last_eid,
                "consumers": {}
            }
            role_info = await me.role_info(stream)
            for role in role_info:
                e_read = role.get("entries-read")  # Entries that have been read and ACK'ed
                e_lag = role.get("lag") or 0       # Entries that have not yet been read by anyone
                e_unacked = role.get("pending") or 0 # Entries that have been read but not ACK'ed yet
                rname = role.get("name", b"??").decode("us-ascii")
                status_dict[stream]["consumers"][rname] = {
                    "read_entries": e_read,
                    "unread_entries": e_lag,
                    "pending_processing": e_unacked,
                }
        except valkey.exceptions.ResponseError:
            pass  # No such pipeline yet..

    return status_dict

# TODO: rate limit feature in asfquart
last_prune = time.time()

@app.route("/prune")
async def prune():
    now = time.time()
    if last_prune > (now - 900):
        return "Too soon, Executus!", 429
    for stream_name in esp.streams.pipes:
        stream = esp.streams.Stream(stream_name)
        print(f"Calling prune on stream {stream_name}")
        await stream.prune()
    return "Pruning scheduled", 201
