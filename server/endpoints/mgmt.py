#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Management endpoint for GDPR operations"""

import plugins.server
import plugins.session
import plugins.mbox
import plugins.defuzzer
import typing
import aiohttp.web
import time


async def process(
    server: plugins.server.BaseServer, session: plugins.session.SessionObject, indata: dict,
) -> typing.Union[dict, aiohttp.web.Response]:
    action = indata.get("action")
    docs = indata.get("documents", [])
    doc = indata.get("document")
    if not docs and doc:
        docs = [doc]
    if not session.credentials.admin or not server.config.ui.mgmt_enabled:
        return aiohttp.web.Response(headers={}, status=403, text="You need administrative access to use this feature.")

    # Deleting/hiding a document?
    if action == "delete":
        delcount = 0
        for doc in docs:
            email = await plugins.mbox.get_email(session, permalink=doc)
            if email and isinstance(email, dict) and plugins.aaa.can_access_email(session, email):
                email["deleted"] = True
                await session.database.index(
                    index=session.database.dbs.mbox, body=email, id=email["id"],
                )
                lid = email.get("list_raw")
                await session.database.index(
                    index=session.database.dbs.auditlog,
                    body={
                        "date": time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(time.time())),
                        "action": "delete",
                        "remote": session.remote,
                        "author": f"{session.credentials.uid}@{session.credentials.oauth_provider}",
                        "target": doc,
                        "lid": lid,
                        "log": f"Removed email {doc} from {lid} archives",
                    },
                )
                delcount += 1
        return aiohttp.web.Response(headers={}, status=200, text=f"Removed {delcount} emails from archives.")
    # Editing an email in place
    elif action == "edit":
        new_from = indata.get("from")
        new_subject = indata.get("subject")
        new_list = "<" + indata.get("list", "").strip("<>").replace("@", ".") + ">"  # foo@bar.baz -> <foo.bar.baz>
        private = True if indata.get("private", "no") == "yes" else False
        new_body = indata.get("body")

        # Check for consistency so we don't pollute the database
        assert isinstance(new_from, str), "Author field must be a text string!"
        assert isinstance(new_subject, str), "Subject field must be a text string!"
        assert isinstance(new_list, str), "List ID field must be a text string!"
        assert isinstance(new_body, str), "Email body must be a text string!"

        email = await plugins.mbox.get_email(session, permalink=doc)
        if email and isinstance(email, dict) and plugins.aaa.can_access_email(session, email):
            email["from_raw"] = new_from
            email["from"] = new_from
            email["subject"] = new_subject
            email["private"] = private
            origin_lid = email["list_raw"]
            email["list"] = new_list
            email["list_raw"] = new_list
            email["body"] = new_body

            # Save edited email
            await session.database.index(
                index=session.database.dbs.mbox, body=email, id=email["id"],
            )

            # Fetch source, mark as deleted (modified) and save
            # We do this, as we can't edit the source easily, so we mark it as off-limits instead.
            source = await plugins.mbox.get_source(session, permalink=email["id"], raw=True)
            if source:
                source = source["_source"]
                source["deleted"] = True
                await session.database.index(
                    index=session.database.dbs.source, body=source, id=email["id"],
                )

            await session.database.index(
                index=session.database.dbs.auditlog,
                body={
                    "date": time.strftime("%Y/%m/%d %H:%M:%S", time.gmtime(time.time())),
                    "action": "edit",
                    "remote": session.remote,
                    "author": f"{session.credentials.uid}@{session.credentials.oauth_provider}",
                    "target": doc,
                    "lid": origin_lid,
                    "log": f"Edited email {doc} from {origin_lid} archives ({origin_lid} -> {new_list})",
                },
            )
            return aiohttp.web.Response(headers={}, status=200, text=f"Email successfully saved")
        return aiohttp.web.Response(headers={}, status=404, text=f"Email not found!")

    return aiohttp.web.Response(headers={}, status=404, text=f"Unknown mgmt command requested")


def register(server: plugins.server.BaseServer):
    return plugins.server.Endpoint(process)
