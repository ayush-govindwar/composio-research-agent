import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)
from composio import Composio

c = Composio(api_key=os.environ['COMPOSIO_API_KEY'])
r = c.tools.execute(
    "EXA_SEARCH",
    user_id="composio_research_agent",
    arguments={"query": "test query", "numResults": 3, "type": "instant"},
    dangerously_skip_version_check=True,
)
print(r)
