"""A measured, optional cap at the generation dispatch boundary."""


class DispatchBudget:
    def __init__(self, limit: int | None = None):
        self.limit = limit
        self.count = 0

    def consume(self):
        if self.limit is not None and self.count >= self.limit:
            raise RuntimeError("Generation request cap reached")
        self.count += 1


def gemini_client(key: str):
    from google import genai

    return genai.Client(api_key=key, http_options={"retry_options": {"attempts": 1}})
