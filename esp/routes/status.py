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
            entries_total = stream_info.get("entries-added", 0)
            role_info = await me.role_info(stream)
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
            for role in role_info:
                e_read = role.get("entries-read")
                e_missing = entries_total - e_read
                rname = role.get("name", b"??").decode("us-ascii")
                status_dict[stream]["consumers"][rname] = {
                    "read_entries": e_read,
                    "unread_entries": e_missing,
                }
        except valkey.exceptions.ResponseError:
            pass  # No such pipeline yet..

    return status_dict

