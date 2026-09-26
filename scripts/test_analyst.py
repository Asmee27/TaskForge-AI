from langchain_core.messages import HumanMessage

from app.agents.data_analyst_agent import (
    build_data_analyst_graph,
)
from app.core.logging import (
    log_event,
    new_run_id,
)


def main():

    graph = build_data_analyst_graph()

    while True:

        question = input(
            "\nBusiness question: "
        ).strip()

        if question.lower() in {
            "exit",
            "quit",
        }:
            break

        if not question:
            continue

        new_run_id()

        log_event(
            "RUN",
            "Analyst workflow started",
        )

        try:

            result = graph.invoke(
                {
                    "messages": [
                        HumanMessage(
                            content=question
                        )
                    ],
                    "step_count": 0,
                    "seen_tool_calls": [],
                },
                config={
                    "recursion_limit": 15
                },
            )

            print(
                "\nANALYST RESPONSE\n"
            )

            print(
                result[
                    "messages"
                ][-1].content
            )

        except Exception as exc:

            print(
                "\nERROR:",
                exc,
            )

        log_event(
            "RUN",
            "Analyst workflow completed",
        )


if __name__ == "__main__":
    main()