import os
import getpass
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)
from composio import Composio

exa_key = getpass.getpass("Paste your Exa API key: ")

c = Composio(api_key=os.environ['COMPOSIO_API_KEY'])
conn = c.connected_accounts.initiate(
    user_id="composio_research_agent",
    auth_config_id="ac_yWoFXKJPjOlS",
    config={"auth_scheme": "API_KEY", "val": {"api_key": exa_key}},
)
print(conn.id, conn.status)
