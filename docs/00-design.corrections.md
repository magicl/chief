# This file contains things that need to be fixed for 00-design.md and 00-design-implementation.md


NOW:
- [x] for LLMSpec we also want to target local providers.. i.e. we should also have a hostname field, etc. each provider should also probably define its available models, and that should likely include the pricing, so pricing is not in a separate file..
- [x] we want the "local" provider to be able to use the openai provider as a base class.. or rather: "LocalOpenAI" is what it should be called.. because it should use all of OpenAI's parsing / formatting functionality, but should add its own host and pricing implementation (based on just power prices)
- [x] we also want to allow providers to be parameterizable.. i.e. there should be a hashmap somewhere that maps provider names to instances of providers
- [x] add some documentation to the locks in channels.py  .. i.e. is it only meant for the tasks RUNNING the agents, or for something else as well?
- [x] add a provider for anthropic as well..
- [x] let's get rid of the "failed" terminal state for sessions. there is no terminal state. errors like provider errors just result in an error.. because we could fix it, restart the server, and then continue the session..

FIX:
- [x] in @loop.py, make_provider should be outside the loop. in the providers, don't check for missing key in the constructor, but rather do it as part of trying to process the message.. have a get_client instance function that creates a client the first time it is called, and then e.g. raise, and have the stream just emit the error to the event stream. i.e. preferably the provider never raises(!) but either deals with the error, by e.g. retries, or adds an error event
- [x] get rid of the _make_provider shell in loop.. just call make_provider directly
- [x] you still have _openai_tools_schema in loop.py .. we want to get anything provider specific into the providers..
- [x] model pricing should also deal with cached input and output tokens often having discounts.. let's create a pydantic model for the return from the model for all providers (custom per provider), and let's validate the return we get from providers with pydantic.. this way it is easier to know the shape  of the data. look at the openai and anthropic documentation to find the shape of htese..
- [x] for the providers, in registry, let's allow ourselves to pass parameters into the provider as listed in registry as well.. specifically for LocalOpenAIProvider, we want to be able to pass in hostname, and the cost parameters in the PROVIDERS list.. we don't want the LLM spec to contain them.. so, suggestion is to make a new function in the providers that takes these provider top level params, change from_spec to take two parameters: (custom-providerspec, LLMSpec), and then in the new function, return from_spec with the first param filled in, but callable as a function to fill the second parameter.. there is a function in the functional library that does that.
- [x] make a local model runner we can use that does not depend on the server, so the agent can verify provider, tools, etc. without the stack running. this CAN be a management function for django. initially, let's make it single turn. I.e. we give some input, and the agent runs till it gets to WAIT, and then we stop and return to the console the return value from the agent.. i.e. all the events are output to the console. Make this command simple, at a minimum, a provider name and a string has to be passed. but also allow instead a full spec to be passed. Allow the agent spec to be served as JSON in this call, and make the internal machinery deal with both yaml and json

MORE TO FIX:
- [x] shouldn't tool definitions in loop.py be captured outside the loop?
- [x] I can't see that you made types for the returns from the openai / anthropic clients..? We want the types for the full return of the models.. not just usage.. and these types are only used inside the provider files themselves, so they should be defined there..
- [x] for PROVIDERS dict.. let the partial be applied inside the providers.. they should have a fucntion we can call, which runs partial on an internal _from_spec.. so we should here call from_spec with the providerconfig, and then from_spec returns partial with _from_spec which is the original fucntion we have now..
- [x] with our run_agent mangement command, we don't want to have to store things to the database.. that will just clutter things up.. To make tests easily mockable, and to allow this one to be good as well.. let's try to make a separate backend.. we can have 3 backends, one for test mocks, and let's use it as well in tests (maybe actually 2 here.. one with DB backing and one without.. maybe), one for the CLI agent, which does not have database backing and does not use redis, and one for the full system.. having a separate interface and implementations here may make the runner loop even simpler..?


EVEN MORE TO FIX:
- [x] search for references to openai / anthropic outside the privider directories.. (tools ourtside is ok, but let's name them test_tools_anthropic for example, rather than test_anthropic_tools)
- [x] UI: on the landing page, instead of one "create demo agent", create a button for each model we have from openai, anthropic and local
- [x] make sure that .env.local is mapped to the backend when running docker compose
- [x] UI: when I hit pause, resume, abort, I get "CSRF token missing"
- [x] UI: instead of a SEND button, lets have ENTER send, and CTRL+enter or SHIFT+enter create a newline

EVEN EVEN MORE TO FIX:
- [x] update our LICENSE file to be apache 2.0 like our file headers
- [x] by creating a "demo_models" thing in the agents app, you created a dependency in agents on providers.. this is not the intent.. this list_demo_models shoudl be in the web app as it is purely display-based.. also get rid of the test for this in the agents app.. also create rules in our AGENTS.md about what apps are allowed to depend on what other apps..  see 00-design and 00-design.impl for references to this..
- [x] get rid of DEFAULT_DEMO_PROVIDER and MODEL in hardcoded.py.. let's not be defensive
- [x] in docker compose, when submitting, i still get an error that no openai credentials are specified..
- [x] add a button to delete an agent
- [x] in the agents table, also include the agent model as a column
- [x] when going into an agent, put focus in the input box immediately


DEBUGGABILITY:
- [x] update 'orun docker compose' to also stream the log to a log file, and write in AGENTS.md that the log can be found there
- [x] also add to AGENTS.md that the docker environment can be logged into using admin/nimda password in localhost/admin first, then go back to the app.. i.e. that the LLM can do this to debug issues.


LATER
- [x] in hardcoded.py, we are defining the code to create the objects for the agent, and the sub-component. we should instead offload this to the code that would normally create an agent based on a spec.. which should be located in the agents app.. look at the 00-design.md and 00-design-implementation.md for hints on how this should be built.. — done: `apps/agents/ingest.py` (`create_agent_from_spec`, `validate_spec_tools`); `hardcoded.py` keeps demo spec only
- [x] would it be better to use uuid7 insteadof uuid4? look at how we do this in ~/yolo/floors/backend — done: model PK defaults use `olib.py.utils.uuid7`
- [x] instead of a register_tool call in our tool implementations.. can't we just have the Tool baseclass have a subclass init function that does this automatically? [?] or have a table where all the tools are instantiated like the models.. [?] maybe this is better as we then can have multiple versions of tools if we need to..? — done: explicit `TOOLS` dict in `apps/agents/tools/registry.py` (no auto-register magic)
- [x] loop._make_provider should probably be in the provider files? and there should probably be a map of providers instead of an if statement? — done: `make_provider()` + `PROVIDERS` dict in `runner/providers/registry.py`; loop calls `make_provider` directly
- [x] preferably we abstract the tools_schema, so it isn't openai-tool schema, but OUR tool schema, and the openai (or other) providers translate that to the schemas needed.. i.e. let's get openai specifics out of the loop.. that should be fully baked into the providers folder.. — done for tools: `ToolDefinition` + `build_tool_definitions()` in agents; `format_tools()` per provider. Remaining: `rebuild_messages()` still uses chat-completions-shaped message dicts (conversation wire format, not tool schema)
- [x] cost computation also needs to take into account caching etc. and price for cached in/out tokens etc. the model will be different per provider, and should be owned by the provider.. — largely done: `Usage` token fields, `ModelPricing` cached/creation rates, default `compute_cost_usd()` on `LLMProvider`; OpenAI/Anthropic populate cache fields; `LocalOpenAIProvider` overrides with power-based cost
