
TODO:
- look at agents.md from floors, and carry over what is useful
- create a docs folder with design
- set up asgi server.. same for prod and compose


Design thoughts:
- Read yaml files from github, similar to github actions
- yaml files define ingest, pipes, agents
- Per agent:
--- Tool call to read from and conclude on item in pipe
--- Able to restrict/allow sub commands for tools. I.e. Can allow gmail read/draft, but not send.
