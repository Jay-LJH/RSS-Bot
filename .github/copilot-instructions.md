Project architecture rules:

1. Data and processing must be separated.

2. All content objects must use the core data model:
   core/article.py

3. Modules must follow this structure:

   sources/
   - responsible for data ingestion
   - convert external data into Article objects

   pipeline/
   - responsible for processing articles
   - each file performs one transformation
   - functions must take Article and return Article

   storage/
   - responsible for persistence only
   - no business logic

   interface/
   - user interaction layer
   - telegram bot, cli, api

4. Do not pass raw dicts between modules.
   Always use Article objects.

5. Pipeline steps must follow the format:

   def run(article: Article) -> Article

6. The pipeline order is controlled in pipeline/runner.py.

7. Avoid putting logic in the core/ directory.
   core contains data structures only.

8. LLM calls must be wrapped in llm/ modules.