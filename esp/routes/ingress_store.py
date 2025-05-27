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
import quart
import esp.streams

app = asfquart.APP

target_stream = esp.streams.Pipelines.INGRES
me = esp.streams.Agent("ingress")


@app.route("/store/<string:initiator>", methods=["POST", "PUT", "GET"])
async def ingress_store(initiator: str):
    """Just plain up stores whatever is sent to it. Uses /store/foo to tag items sent from foo."""
    data = await asfquart.utils.formdata()
    headers = dict({k.lower():v for k,v in quart.request.headers.items()})
    headers.pop("cookie", "")  # We never want bad cookies, blech
    if data:
        # If we got something sent, log it to the pipeline.
        entry = esp.streams.Stream.Entry(initiator=f"inbound::{initiator}", headers=headers)
        entry.data = data
        eid = await me.write(target_stream, entry)
        return f"Queued as {initiator}::{eid}", 201

    return f"Hello, {quart.request.remote_addr}! What are you doing here?", 400


@app.route("/peek")
async def peek():
    entry = await me.read(target_stream).__anext__()
    await entry.complete()
    return entry.data

