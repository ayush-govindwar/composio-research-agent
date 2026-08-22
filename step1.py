import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)
from composio import Composio

c = Composio(api_key=os.environ['COMPOSIO_API_KEY'])
ac = c.auth_configs.create(
    toolkit="exa",
    options={
        "type": "use_custom_auth",
        "auth_scheme": "API_KEY",
        "name": "Exa API Key",
        "credentials": {},
    },
)
print(ac.id)
