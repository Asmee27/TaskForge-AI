from __future__ import annotations
from langchain_core.messages import HumanMessage
from app.agents.research_agent import build_research_graph
from app.core.logging import log_event, new_run_id

def run_once(graph, user_input: str) -> str:
    new_run_id(); log_event('RUN','Started')
    result = graph.invoke({'messages':[HumanMessage(content=user_input)]})
    log_event('RUN','Completed')
    return result['messages'][-1].content

def main():
    graph = build_research_graph()
    print('\nSynapseOps AI - Research Agent')
    print('External business research only for now. Type \'exit\' to quit.\n')
    while True:
        user_input=input('User: ').strip()
        if user_input.lower() in {'exit','quit'}: break
        if not user_input: continue
        try: print('\nAgent:', run_once(graph,user_input), '\n')
        except Exception as exc: print(f'\nApplication error: {exc}\n')
if __name__ == '__main__': main()
