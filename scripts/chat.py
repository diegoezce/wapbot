from app.agent import create_agent
from app.db import initialize_database


def main() -> None:
    initialize_database()
    agent = create_agent()
    phone = "terminal-demo"
    print("Demo Clinic chat. Type /quit to exit.")
    while True:
        try:
            message = input("You: ").strip()
        except EOFError:
            break
        if message.lower() in {"/quit", "/exit"}:
            break
        if not message:
            continue
        print(f"Clinic: {agent.respond(phone, message)}")


if __name__ == "__main__":
    main()
