"""Composio v3 session wiring for the research pipeline."""

import os
import warnings

_session = None
_import_error = None

USER_ID = "composio_research_agent"

EXA_SEARCH = "EXA_SEARCH"
EXA_FETCH = "EXA_GET_CONTENTS_ACTION"

try:
    from composio import Composio, SESSION_PRESET_DIRECT_TOOLS
    from composio_langchain import LangchainProvider
except Exception as e:
    Composio = None
    SESSION_PRESET_DIRECT_TOOLS = None
    _import_error = e


def _get_session():
    global _session

    if _session is not None:
        return _session

    if Composio is None:
        warnings.warn(
            f"Composio import failed: {_import_error}"
        )
        return None

    if SESSION_PRESET_DIRECT_TOOLS is None:
        warnings.warn(
            "Installed Composio SDK does not support direct-tools preset."
        )
        return None

    api_key = os.environ.get("COMPOSIO_API_KEY")

    if not api_key:
        warnings.warn("COMPOSIO_API_KEY is not set.")
        return None

    try:
        client = Composio(
            api_key=api_key,
            provider=LangchainProvider(),
        )

        # ---------------------------------------------------------
        # Find the ACTIVE Exa connection belonging to THIS user.
        # ---------------------------------------------------------
        accounts = client.connected_accounts.list(
            user_ids=[USER_ID],
            statuses=["ACTIVE"],
        )

        exa_accounts = [
            account
            for account in accounts.items
            if account.toolkit.slug.lower() == "exa"
            and account.status == "ACTIVE"
        ]

        if not exa_accounts:
            warnings.warn(
                f"No ACTIVE Exa connected account found for user "
                f"'{USER_ID}'. Connect Exa in Composio first."
            )
            return None

        # Use the most recently created active Exa account.
        exa_account = sorted(
            exa_accounts,
            key=lambda x: x.created_at or "",
            reverse=True,
        )[0]

        print(
            f"Using Exa connected account: "
            f"{exa_account.id}"
        )

        # ---------------------------------------------------------
        # IMPORTANT:
        # Explicitly pin this session to the Exa account.
        # ---------------------------------------------------------
        _session = client.create(
            user_id=USER_ID,

            connected_accounts={
                "exa": [exa_account.id],
            },

            toolkits=["EXA"],

            tools={
                "exa": {
                    "enable": [
                        EXA_SEARCH,
                        EXA_FETCH,
                    ]
                }
            },

            session_preset=SESSION_PRESET_DIRECT_TOOLS,

            sandbox={
                "enable": False,
            },
        )

        return _session

    except Exception as e:
        warnings.warn(
            f"Failed to create Composio session: {e}"
        )
        return None


def _tools_by_name():
    session = _get_session()

    if session is None:
        return {}

    try:
        tools = session.tools()

        return {
            getattr(tool, "name", ""): tool
            for tool in tools
        }

    except Exception as e:
        warnings.warn(
            f"Could not load Composio tools: {e}"
        )
        return {}


def get_search_tools():
    tool = _tools_by_name().get(EXA_SEARCH)

    return [tool] if tool is not None else []


def get_fetch_tools():
    tool = _tools_by_name().get(EXA_FETCH)

    return [tool] if tool is not None else []


def composio_available() -> bool:
    return bool(
        Composio is not None
        and os.environ.get("COMPOSIO_API_KEY")
        and get_search_tools()
    )