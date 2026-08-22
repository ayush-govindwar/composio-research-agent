import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)
from composio import Composio

c = Composio(api_key=os.environ['COMPOSIO_API_KEY'])
r = c.connected_accounts.list(user_ids=['composio_research_agent'])
[print(x.id, x.toolkit.slug, x.status) for x in r.items]
