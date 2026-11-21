"""A tab sums model stretches. Only the open stretch owns the context window."""

from aconsole.core.application.services.token_ledger import TokenBook, TokenReply


def test_spend_sums_across_clears_and_the_window_belongs_to_the_open_model() -> None:
    book = TokenBook()
    book.note(
        TokenReply(model="sonnet", fresh_input=1000, cache_read=200, output=50, window_pct=40, cost_total=1.0),
        generation=0,
        session="s1",
        scope="session",
    )
    book.note(
        TokenReply(model="sonnet", fresh_input=400, output=20, window_pct=55, cost_total=1.25),
        generation=0,
        session="s1",
        scope="session",
    )
    assert book.close()
    book.note(
        TokenReply(model="sonnet", fresh_input=100, output=10, window_pct=8, cost_total=0.3),
        generation=1,
        session="s2",
        scope="session",
    )
    assert book.spent() == 1000 + 200 + 50 + 400 + 20 + 100 + 10
    assert book.cost() == 1.55
    open_tasks = [task for task in book.tasks if task.open]
    assert len(open_tasks) == 1
    assert open_tasks[0].window_pct == 8
    assert open_tasks[0].generation == 1


def test_a_repeated_reply_and_a_side_model_do_not_take_the_window() -> None:
    book = TokenBook()
    reply = TokenReply(
        model="gpt", fresh_input=10, output=2, window_tokens=500, window_size=1000, key="a", primary=True
    )
    book.note(reply, generation=0, session="s", scope="session")
    again = book.note(reply, generation=0, session="s", scope="session")
    book.note(
        TokenReply(model="helper", fresh_input=7, output=1, key="b", primary=False),
        generation=0,
        session="s",
        scope="session",
    )
    assert again.spent is False
    assert book.spent() == 20
    primary = next(task for task in book.tasks if task.primary)
    side = next(task for task in book.tasks if not task.primary)
    assert primary.window_pct == 50
    assert primary.open is True
    assert side.window_pct is None
    assert side.spent() == 8


def test_a_running_tab_total_restarts_only_when_the_reported_total_drops() -> None:
    book = TokenBook()
    book.note(TokenReply(model="opencode", cost_total=1.0), generation=0, session="s", scope="tab")
    book.note(TokenReply(cost_total=1.4), generation=0, session="s", scope="tab")
    book.note(TokenReply(cost_total=0.2), generation=1, session="s2", scope="tab")
    assert book.cost() == 1.6
