# SynapseOps AI
Hackathon project: Multi-Agent Business Decision & Action Platform.

## Current milestone
Only the Research Agent is implemented in this clean base. It handles external/current business research using Tavily.
Internal company analytics will be added later using the Olist e-commerce dataset converted into SQLite.

## Planned final agents
- Planner Agent
- Research Agent
- Data Analyst Agent
- Action Agent
- Safety/Critic Agent

## Data
Place the original Olist CSV files unchanged inside `data/raw/`. Do not rename or edit them yet.

## Setup (Windows)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

Copy `.env.example` to `.env` and add your keys.

Run:
python main.py

Example test:
What are the latest AI customer-support trends for e-commerce businesses?

The Research Agent must NOT claim to know internal company sales, customers, products, inventory, or revenue. That belongs to the future Data Analyst Agent.
