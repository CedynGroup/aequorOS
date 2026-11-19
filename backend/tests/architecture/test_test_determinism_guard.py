"""Snippet table for the AQPY determinism guard and its shrinking allowlist."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from scripts import test_determinism_guard as guard

BACKEND = Path(__file__).resolve().parents[2]
INSTANT = '"2026-01-01T00:00:00+00:00"'

#: Reads the guard must reject: direct members, both datetime import styles and
#: aliased imports of the platform utc_now helpers.
UNFROZEN_READS = [
    "from datetime import datetime\ndef test_now():\n    datetime.now()\n",
    "from datetime import datetime, UTC\ndef test_now_tz():\n    datetime.now(tz=UTC)\n",
    "from datetime import datetime\ndef test_utcnow():\n    datetime.utcnow()\n",
    "from datetime import datetime\ndef test_today():\n    datetime.today()\n",
    "import datetime\ndef test_module_style():\n    datetime.datetime.now()\n",
    "from datetime import date\ndef test_date_today():\n    date.today()\n",
    "import time\ndef test_time():\n    time.time()\n",
    "from time import time as clock\ndef test_aliased():\n    clock()\n",
    "from app.db.base import utc_now\ndef test_platform_utc_now():\n    utc_now()\n",
    "from app.db.base import utc_now as now\ndef test_renamed():\n    now()\n",
    "from app.attestation.service import api as attestation_api\n"
    "def test_attestation():\n"
    "    attestation_api.utc_now()\n",
    "def test_rebinding():\n    from app.db.base import utc_now\n    read = utc_now\n    read()\n",
    # A ticking travel does not pin an instant, and travel(None, tick=False)
    # freezes at the host clock read on entry; neither accepts its reads.
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_ticking_travel():\n"
    "    with time_machine.travel(DESTINATION):\n"
    "        datetime.now()\n",
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_host_frozen_travel():\n"
    "    with time_machine.travel(None, tick=False):\n"
    "        datetime.now()\n",
    # A marker freezes test execution, not import time: a module-level read
    # still runs against the host clock.
    "import pytest\n"
    "from datetime import datetime, UTC\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
    "STAMP = datetime.now(UTC)\n",
    # A conditional rebinding keeps the clock binding until every reachable
    # path replaces it: the fall-through path still reads the wall clock.
    "import time as clock\n"
    "import fake_clock\n"
    "if use_fake():\n"
    "    clock = fake_clock\n"
    "def test_conditional_rebind():\n"
    "    clock.time()\n",
    "import fake_clock\n"
    "def test_local_conditional_rebind(use_fake):\n"
    "    import time as clock\n"
    "    if use_fake:\n"
    "        clock = fake_clock\n"
    "    clock.time()\n",
    # Two branches may hold different clock bindings; every clock-capable
    # possibility stays reported until all paths replace the binding.
    "def test_two_clock_bindings(use_datetime):\n"
    "    if use_datetime:\n"
    "        import datetime as clock\n"
    "    else:\n"
    "        import time as clock\n"
    "    if not use_datetime:\n"
    "        clock.time()\n",
    # A conditional expression selects the clock the same way an if statement
    # rebinds a name; the unselected path must not hide the clock callable.
    "from datetime import datetime\n"
    "def test_conditional_expression(use_real):\n"
    "    clock = datetime.now if use_real else fake_clock\n"
    "    clock()\n",
    # An attribute alias through a conditionally bound name resolves
    # alternative by alternative, so an intermediate variable cannot drop the
    # clock member.
    "def test_attribute_alias(use_time):\n"
    "    if use_time:\n"
    "        import time as clock\n"
    "    else:\n"
    "        import datetime as clock\n"
    "    read = clock.time\n"
    "    read()\n",
    # A boolean fallback yields one of its operands, so the real-clock
    # operand keeps the binding detectable.
    "from datetime import datetime\n"
    "def test_boolop_fallback(fake):\n"
    "    clock = fake_clock or datetime.now\n"
    "    clock()\n",
    # A boolean expression may also be the invoked callable itself.
    "import time\ndef test_direct_boolop_call():\n    (time.time or None)()\n",
    # A body assignment carries into later iterations: a read before the
    # assignment still executes against the assigned clock.
    "import time\nclock = lambda: 0\nfor _ in (1, 2):\n    clock()\n    clock = time.time\n",
    # A match case may be skipped, so the pre-match binding stays reachable.
    "import time\n"
    "def test_match_case(mode):\n"
    "    clock = time.time\n"
    "    match mode:\n"
    "        case 'fake':\n"
    "            clock = fake_clock\n"
    "    clock()\n",
    # A while condition is re-evaluated every iteration under the body's
    # assignments.
    "import time\n"
    "clock = lambda: True\n"
    "iteration = 0\n"
    "while clock() and iteration < 2:\n"
    "    clock = time.time\n"
    "    iteration += 1\n",
    # A short-circuited operand may not execute, so its walrus assignment
    # cannot mask the pre-expression clock binding.
    "import time\n"
    "def test_boolop_walrus(enabled):\n"
    "    clock = time.time\n"
    "    enabled and (clock := lambda: 0)\n"
    "    clock()\n",
    # A chained comparison short-circuits the same way: a skipped
    # comparator's walrus cannot mask the clock binding either.
    "import time\n"
    "def test_chained_comparison_walrus():\n"
    "    clock = time.time\n"
    "    chained = 1 > 2 > (clock := lambda: 0)\n"
    "    clock()\n",
    # Either branch of a conditional expression may run, so a walrus in one
    # branch cannot mask the pre-expression clock binding either.
    "import time\n"
    "def test_ifexp_walrus(flag):\n"
    "    clock = time.time\n"
    "    value = (clock := lambda: 0) if flag else None\n"
    "    clock()\n",
    # An annotated assignment evaluates its value before its annotation, so
    # the annotation's walrus cannot mask the value's own read.
    "import time\nclock = time.time\nstamp: (clock := lambda: 0) = clock()\n",
    # A loop body may run zero times, so its assignments never clear a
    # clock binding.
    "from datetime import datetime\n"
    "clock = datetime.now\n"
    "for replacement in ():\n"
    "    clock = replacement\n"
    "clock()\n",
    # A ticking travel inside a frozen travel advances the clock from real
    # elapsed time; its reads are not pinned to an instant.
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_ticking_inside_frozen():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    f"        with time_machine.travel({INSTANT}):\n"
    "            datetime.now()\n",
    # Entry order decides the freeze state: after a ticking travel un-freezes
    # the entry list, a later manager runs against the live clock again.
    "from contextlib import nullcontext\n"
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_manager_after_ticking_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=False), time_machine.travel({INSTANT}), "
    "nullcontext(datetime.now()):\n"
    "        pass\n",
    # A deferred body defined inside a travel block may be called after the
    # block exits, when the travel is gone; only a marker or the frozen_clock
    # fixture covers a whole test execution.
    "import time_machine\n"
    "from datetime import datetime\n"
    f"def test_lambda_inside_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        return lambda: datetime.now()\n",
    "import time\n"
    "import time_machine\n"
    "from datetime import datetime\n"
    f"with time_machine.travel({INSTANT}, tick=False):\n"
    "    values = (time.time() for _ in (1,))\n"
    "next(values)\n",
    "import time_machine\n"
    "from datetime import datetime\n"
    f"with time_machine.travel({INSTANT}, tick=False):\n"
    "    def test_defined_inside_travel():\n"
    "        datetime.now()\n",
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_callback_after_the_block():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        callback = lambda: datetime.now()\n"
    "    callback()\n",
    # A direct break keeps the else block unreachable, so the pre-loop clock
    # binding survives.
    "import time\n"
    "def test_direct_break_skips_else():\n"
    "    clock = time.time\n"
    "    for _ in (1,):\n"
    "        break\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A capture inherits the matched subject, so a clock subject's capture
    # still reads the wall clock.
    "import time\n"
    "def test_match_capture_inherits_subject():\n"
    "    match time.time:\n"
    "        case clock:\n"
    "            clock()\n",
    # Dictionary entries evaluate key and value in order, so a later key's
    # walrus cannot mask an earlier value's read.
    "import time\n"
    "def test_dict_entry_order():\n"
    "    clock = time.time\n"
    "    d = {0: clock(), (clock := lambda: 0): 1}\n",
    # Assignment chains settle across iterations, so a later iteration can
    # read a clock binding propagated through another alias.
    "import time\n"
    "def test_alias_chain_across_iterations():\n"
    "    a = b = lambda: 0\n"
    "    for _ in range(3):\n"
    "        a()\n"
    "        a = b\n"
    "        b = time.time\n",
    # A comprehension may run zero iterations, so its walrus cannot replace
    # the incoming binding.
    "import time\n"
    "def test_empty_comprehension_walrus():\n"
    "    clock = time.time\n"
    "    [(clock := lambda: 0) for _ in []]\n"
    "    clock()\n",
    # A handler may run without the exception, so its rebinding cannot
    # clear the normal path's binding.
    "import time\n"
    "def test_handler_rebind_keeps_normal_path():\n"
    "    clock = time.time\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # An exception may surface before the body rebinds, so a handler read
    # still sees the pre-try binding.
    "import time\n"
    "def test_handler_read_keeps_pre_try_binding():\n"
    "    clock = time.time\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError:\n"
    "        clock()\n",
    # An exception raised after a body rebind reaches the handler with that
    # binding, so the handler's read is reported.
    "import time\n"
    "def test_handler_sees_intermediate_binding():\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        clock = time.time\n"
    "        raise ValueError\n"
    "    except ValueError:\n"
    "        clock()\n",
    # Zero iterations leave the loop target holding its incoming value.
    "import time\n"
    "def test_zero_iterations_keep_the_target():\n"
    "    clock = time.time\n"
    "    for clock in []:\n"
    "        pass\n"
    "    clock()\n",
    # A comprehension's walrus feeds the next iteration, so the second
    # iteration's read is reported.
    "import time\n"
    "def test_comprehension_walrus_feeds_back():\n"
    "    clock = lambda: 0\n"
    "    [(clock(), (clock := time.time)) for _ in range(2)]\n",
    # A first-iteration read stays reported even when the element's walrus
    # replaces the clock for later iterations.
    "import time\n"
    "def test_comprehension_first_iteration_read():\n"
    "    clock = time.time\n"
    "    [(clock(), (clock := lambda: 0)) for _ in range(1)]\n",
    # A break exits with the bindings at the break, not the body's end.
    "import time\n"
    "def test_break_keeps_its_binding(flag):\n"
    "    clock = lambda: 0\n"
    "    for _ in (1,):\n"
    "        clock = time.time\n"
    "        if flag:\n"
    "            break\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A false filter rejects the iteration, so the element's walrus cannot
    # overwrite the filter's binding.
    "import time\n"
    "def test_filter_exit_keeps_its_binding():\n"
    "    clock = lambda: 0\n"
    "    [(clock := lambda: 0) for _ in (1,) if (clock := time.time) and False]\n"
    "    clock()\n",
    # A passing filter keeps the element's own assignment reachable too.
    "import time\n"
    "def test_successful_iteration_keeps_its_binding():\n"
    "    clock = lambda: 0\n"
    "    [(clock := time.time) for _ in (1,) if True]\n"
    "    clock()\n",
    # A continue hands its bindings to the next iteration.
    "import time\n"
    "def test_continue_carries_its_binding(flag):\n"
    "    clock = lambda: 0\n"
    "    for _ in (1, 2):\n"
    "        clock()\n"
    "        clock = time.time\n"
    "        if flag:\n"
    "            continue\n"
    "        clock = lambda: 0\n",
    # A failed guard's bindings carry into the later cases.
    "import time\n"
    "def test_failed_guard_carries_binding():\n"
    "    clock = lambda: 0\n"
    "    match 1:\n"
    "        case 1 if (clock := time.time) and False:\n"
    "            pass\n"
    "        case _:\n"
    "            clock()\n",
    # A failed guard's bindings also survive a fall-through match.
    "import time\n"
    "def test_failed_guard_survives_fallthrough():\n"
    "    clock = lambda: 0\n"
    "    match 1:\n"
    "        case 1 if (clock := time.time) and False:\n"
    "            clock = lambda: 0\n"
    "    clock()\n",
    # A break recorded before a try statement does not cross its finally.
    "import time\n"
    "def test_break_before_try_skips_finally(flag):\n"
    "    clock = lambda: 0\n"
    "    for _ in (1,):\n"
    "        clock = time.time\n"
    "        if flag:\n"
    "            break\n"
    "        try:\n"
    "            pass\n"
    "        finally:\n"
    "            clock = lambda: 0\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # The normal path through a try keeps its own bindings after the
    # break-state transform.
    "import time\n"
    "def test_normal_path_after_break_transform(flag):\n"
    "    clock = lambda: 0\n"
    "    for _ in (1,):\n"
    "        try:\n"
    "            if flag:\n"
    "                break\n"
    "            clock = time.time\n"
    "        finally:\n"
    "            pass\n"
    "    clock()\n",
    # A finally read reached only through a jump state is still reported.
    "import time\n"
    "def test_finally_read_through_break(flag):\n"
    "    clock = lambda: 0\n"
    "    for _ in (1,):\n"
    "        try:\n"
    "            clock = time.time\n"
    "            if flag:\n"
    "                break\n"
    "            clock = lambda: 0\n"
    "        finally:\n"
    "            clock()\n",
    # A nested comprehension's walrus reaches the containing scope.
    "import time\n"
    "def test_nested_comprehension_walrus():\n"
    "    clock = lambda: 0\n"
    "    [[(clock := time.time) for y in (1,)] for x in (1,)]\n"
    "    clock()\n",
    # A generator's filters also run at consumption time, so a filter read
    # is reported even when created inside a frozen travel.
    "import time\n"
    "import time_machine\n"
    "from datetime import datetime\n"
    f"with time_machine.travel({INSTANT}, tick=False):\n"
    "    values = (0 for _ in (1,) if time.time())\n"
    "next(values)\n",
    # An inner comprehension iteration keeps its own carried bindings
    # against the outer filter's reassignment.
    "import time\n"
    "def test_nested_comprehension_levels():\n"
    "    clock = lambda: 0\n"
    "    [(clock(), (clock := time.time)) for x in (1,) if (clock := lambda: 0) for y in (1, 2)]\n",
    # A deferred generator's element settles across its iterations the same
    # way an eager comprehension's does.
    "import time\n"
    "def test_deferred_generator_carried():\n"
    "    clock = lambda: 0\n"
    "    values = ((clock(), (clock := time.time)) for _ in (1, 2))\n"
    "    list(values)\n",
    # A loop over a literal iterable of clock callables keeps the target
    # resolvable on every iteration.
    "import time\n"
    "def test_loop_target_known_elements():\n"
    "    for clock in (time.time,):\n"
    "        clock()\n",
    # A parameter that defaults to a clock callable holds it in the body,
    # whether positional, keyword-only or on a lambda.
    "import time\ndef test_positional_default(clock=time.time):\n    clock()\n",
    "import time\ndef test_keyword_only_default(*, clock=time.time):\n    clock()\n",
    "import time\ndef test_lambda_default():\n    (lambda clock=time.time: clock())()\n",
    # A freeze marker on a fixture function never reaches the tests that
    # request it, so the fixture body still reads the real clock.
    "import pytest\n"
    "import time\n"
    f"@pytest.fixture\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def stamp():\n"
    "    return time.time()\n",
    # A return crosses the finally block with the bindings at the return.
    "import time\n"
    "def test_return_through_finally(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        clock = time.time\n"
    "        if flag:\n"
    "            return\n"
    "        clock = lambda: 0\n"
    "    finally:\n"
    "        clock()\n",
    # An exception exit runs the finally block under the bindings at the
    # raise point.
    "import time\n"
    "def test_raise_through_finally(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        clock = time.time\n"
    "        if flag:\n"
    "            raise ValueError\n"
    "        clock = lambda: 0\n"
    "    finally:\n"
    "        clock()\n",
    # An exception raised in the else block runs the finally block under the
    # bindings at the raise point.
    "import time\n"
    "def test_raise_in_else_through_finally(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError:\n"
    "        pass\n"
    "    else:\n"
    "        clock = time.time\n"
    "        if flag:\n"
    "            raise ValueError\n"
    "        clock = lambda: 0\n"
    "    finally:\n"
    "        clock()\n",
    # An exception raised inside a compound statement of the try body runs
    # the finally block under the bindings at the raise point.
    "import time\n"
    "from contextlib import nullcontext\n"
    "def test_raise_in_with_through_finally(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        with nullcontext():\n"
    "            clock = time.time\n"
    "            if flag:\n"
    "                raise ValueError\n"
    "            clock = lambda: 0\n"
    "    finally:\n"
    "        clock()\n",
    # Each default is captured before the later defaults evaluate.
    "import time\ndef test_earlier_default(clock=time.time, other=(time := None)):\n    clock()\n",
    # A handler starts from the bindings at every point of the try body,
    # including inside its compound statements.
    "import time\n"
    "from contextlib import nullcontext\n"
    "def test_raise_in_with_into_handler(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        with nullcontext():\n"
    "            clock = time.time\n"
    "            if flag:\n"
    "                raise ValueError\n"
    "            clock = lambda: 0\n"
    "    except ValueError:\n"
    "        clock()\n",
    # An exception escaping a nested try reaches the enclosing handler under
    # the bindings at its raise point.
    "import time\n"
    "def test_nested_try_escapes_into_handler(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        try:\n"
    "            clock = time.time\n"
    "            if flag:\n"
    "                raise ValueError\n"
    "            clock = lambda: 0\n"
    "        finally:\n"
    "            pass\n"
    "    except ValueError:\n"
    "        clock()\n",
    # An assignment expression yields the value it binds.
    "import time\n"
    "def test_named_expression_alias():\n"
    "    clock = (reader := time.time)\n"
    "    clock()\n",
    # A handler that does not match leaves the nested exception escaping
    # under the bindings at its raise point.
    "import time\n"
    "def test_nonmatching_inner_handler(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        try:\n"
    "            clock = time.time\n"
    "            if flag:\n"
    "                raise ValueError\n"
    "            clock = lambda: 0\n"
    "        except TypeError:\n"
    "            clock = lambda: 0\n"
    "    except ValueError:\n"
    "        clock()\n",
    # A generator's first iterable evaluates at creation, so its element
    # binding survives a later rebinding of the alias.
    "import time\n"
    "def test_first_iterable_captured_at_creation():\n"
    "    clock = time.time\n"
    "    values = (reader() for reader in (clock,))\n"
    "    clock = lambda: 0\n"
    "    list(values)\n",
    # An exception between the subexpressions of one statement reaches the
    # handler under the bindings made so far.
    "import time\n"
    "def test_exception_inside_an_expression():\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        ((clock := time.time), 1 / 0, (clock := lambda: 0))\n"
    "    except ZeroDivisionError:\n"
    "        clock()\n",
    # An Exception handler leaves a BaseException escaping.
    "import time\n"
    "def test_exception_handler_leaves_system_exit(flag):\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        try:\n"
    "            clock = time.time\n"
    "            if flag:\n"
    "                raise SystemExit\n"
    "            clock = lambda: 0\n"
    "        except Exception:\n"
    "            clock = lambda: 0\n"
    "    except SystemExit:\n"
    "        clock()\n",
    # Consuming a generator may run its assignment expressions, so the
    # enclosing scope holds their bindings afterwards.
    "import time\n"
    "def test_generator_walrus_after_consumption():\n"
    "    clock = lambda: 0\n"
    "    values = ((clock := time.time) for _ in (1,))\n"
    "    list(values)\n"
    "    clock()\n",
    # A generator consumed in place binds in the enclosing scope too.
    "import time\n"
    "def test_generator_walrus_consumed_in_place():\n"
    "    clock = lambda: 0\n"
    "    list((clock := time.time) for _ in (1,))\n"
    "    clock()\n",
    # The generator's assigned value resolves under the bindings at
    # consumption, not at creation.
    "import time\n"
    "def test_generator_walrus_value_at_consumption():\n"
    "    reader = lambda: 0\n"
    "    clock = lambda: 0\n"
    "    values = ((clock := reader) for _ in (1,))\n"
    "    reader = time.time\n"
    "    list(values)\n"
    "    clock()\n",
    # The generator's assigned value resolves its own iteration target.
    "import time\n"
    "def test_generator_walrus_iteration_target():\n"
    "    clock = lambda: 0\n"
    "    values = ((clock := reader) for reader in (time.time,))\n"
    "    list(values)\n"
    "    clock()\n",
    # A generator consumed in place reads under the bindings at consumption,
    # not those left by a later rebinding.
    "import time\n"
    "def test_generator_consumed_before_rebinding():\n"
    "    clock = time.time\n"
    "    list(clock() for _ in (1,))\n"
    "    clock = lambda: 0\n",
    # Unpacking captures each element as it evaluates.
    "import time\nclock, ignored = time.time, (time := None)\nclock()\n",
    # A module-level generator consumed before the alias is replaced reads
    # the real clock at import.
    "import time\nclock = time.time\nlist(clock() for _ in (1,))\nclock = lambda: 0\n",
    # Copying a generator variable keeps it unconsumed: the read happens
    # where the copy is consumed.
    "import time\n"
    "def test_generator_alias_consumed_late():\n"
    "    clock = lambda: 0\n"
    "    values = (clock() for _ in (1,))\n"
    "    alias = values\n"
    "    clock = time.time\n"
    "    list(alias)\n",
    # A ticking travel does not revive the marker freeze for a generator
    # consumed inside it.
    "import pytest\n"
    "import time\n"
    "import time_machine\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def test_generator_consumed_in_ticking_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=True):\n"
    "        list(time.time() for _ in (1,))\n",
    # An augmented assignment evaluates its target before its value.
    "import time\n"
    "from collections import defaultdict\n"
    "def test_augmented_target_before_value():\n"
    "    values = defaultdict(int)\n"
    "    clock = time.time\n"
    "    values[clock()] += (clock := lambda: 0)()\n",
    # A later iterable supplies its target at consumption.
    "import time\n"
    "def test_later_iterable_resolved_at_consumption():\n"
    "    clock = lambda: 0\n"
    "    first = lambda: 0\n"
    "    values = ((clock := reader) for _ in (1,) for reader in (first,))\n"
    "    first = time.time\n"
    "    list(values)\n"
    "    clock()\n",
    # Unpacked loop and comprehension targets keep the callables the literal
    # elements supply.
    "import time\nfor (clock,) in ((time.time,),):\n    clock()\n",
    "import time\n[clock() for (clock,) in ((time.time,),)]\n",
    # An annotated copy of a generator variable keeps it unconsumed.
    "import time\n"
    "def test_annotated_generator_alias_consumed_late():\n"
    "    clock = lambda: 0\n"
    "    values = (clock() for _ in (1,))\n"
    "    alias: object = values\n"
    "    clock = time.time\n"
    "    list(alias)\n",
    # A later iterable may name an earlier target of the same generator.
    "import time\n"
    "def test_dependent_generator_targets():\n"
    "    clock = lambda: 0\n"
    "    values = ((clock := reader) for first in (time.time,) for reader in (first,))\n"
    "    list(values)\n"
    "    clock()\n",
    # An assignment expression's result is what it evaluated, not what its
    # target replaced.
    "import time\nclock = (time := time.time)\nclock()\n",
    # A generator created on one branch stays reachable after the join.
    "import time\n"
    "def test_conditional_generator(flag):\n"
    "    clock = lambda: 0\n"
    "    if flag:\n"
    "        values = (clock() for _ in (1,))\n"
    "    else:\n"
    "        values = []\n"
    "    clock = time.time\n"
    "    list(values)\n",
    # A freeze marker on a helper does not freeze it: pytest only reads
    # marks of the items it collects.
    "import pytest\n"
    "import time\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def helper():\n"
    "    return time.time()\n",
    # Consuming a generator runs its filters before its element.
    "import time\n"
    "def test_filter_before_element():\n"
    "    clock = lambda: 0\n"
    "    values = ((clock := reader) for _ in (1,) if (reader := time.time))\n"
    "    list(values)\n"
    "    clock()\n",
    # A defaulted parameter is not a fixture request.
    "import time\ndef test_defaulted_frozen_clock(frozen_clock=None):\n    return time.time()\n",
    # Nested elements of an unpacked value are captured as they evaluate.
    "import time\n(clock,), other = (time.time,), (time := None)\nclock()\n",
    # A loop iterable's elements are captured as they evaluate.
    "import time\nfor clock in (time.time, (time := lambda: 0)):\n    clock()\n",
    # A sequence pattern captures each matched element.
    "import time\nmatch (time.time,):\n    case (clock,):\n        clock()\n",
    # Direct parametrization replaces the fixture.
    "import pytest\n"
    "import time\n"
    "@pytest.mark.parametrize('frozen_clock', [None])\n"
    "def test_parametrized_frozen_clock(frozen_clock):\n"
    "    return time.time()\n",
    # Starred unpacking keeps the fixed positions around the star.
    "import time\nclock, *rest = (time.time,)\nclock()\n",
    # A sequence-valued indirect that omits the argument leaves it direct.
    "import pytest\n"
    "import time\n"
    "@pytest.mark.parametrize('frozen_clock', [None], indirect=[])\n"
    "def test_empty_indirect(frozen_clock):\n"
    "    return time.time()\n",
    # A starred sequence pattern keeps the fixed positions around the star.
    "import time\nmatch (time.time,):\n    case (clock, *rest):\n        clock()\n",
    # A helper is not injected with fixtures, whatever its parameter is named.
    "import time\ndef helper(frozen_clock):\n    return time.time()\n",
    # Class and module parametrization replace the fixture too.
    "import pytest\n"
    "import time\n"
    "@pytest.mark.parametrize('frozen_clock', [None])\n"
    "class TestRead:\n"
    "    def test_read(self, frozen_clock):\n"
    "        return time.time()\n",
    "import pytest\n"
    "import time\n"
    "pytestmark = pytest.mark.parametrize('frozen_clock', [None])\n"
    "def test_read(frozen_clock):\n"
    "    return time.time()\n",
    # An ``as`` capture holds the matched subject.
    "import time\nmatch time.time:\n    case clock as other:\n        other()\n",
    # A generator unpacked into a target stays unconsumed until the target is.
    "import time\n"
    "def test_unpacked_generator():\n"
    "    clock = lambda: 0\n"
    "    values, = ((clock() for _ in (1,)),)\n"
    "    clock = time.time\n"
    "    list(values)\n",
    # A held generator unpacked into a target stays unconsumed.
    "import time\n"
    "def test_unpacked_held_generator():\n"
    "    clock = lambda: 0\n"
    "    values = (clock() for _ in (1,))\n"
    "    alias, = (values,)\n"
    "    clock = time.time\n"
    "    list(alias)\n",
    # A marked helper class is not collected, so its mark freezes nothing.
    "import pytest\n"
    "import time\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "class Helper:\n"
    "    def read(self):\n"
    "        return time.time()\n",
    # Any alternative of an OR pattern may be the one that matched.
    "import time\n"
    "match (time.time, None):\n"
    "    case (clock, None) | (_, clock):\n"
    "        clock()\n",
    # A call's own expression establishes the callable it invokes.
    "import time\n"
    "def test_callable_from_its_own_expression():\n"
    "    (reader if (reader := time.time) else None)()\n",
    # A mapping pattern captures the literal subject's value for its key.
    "import time\nmatch {'clock': time.time}:\n    case {'clock': clock}:\n        clock()\n",
    # A direct call through an assignment expression invokes what it bound.
    "import time\n(time := time.time)()\n",
    # A dictionary value is captured as it evaluates.
    "import time\n"
    "match {'clock': time.time, 'other': (time := None)}:\n"
    "    case {'clock': clock}:\n"
    "        clock()\n",
    # A test-named function nested in another body is not collected.
    "import pytest\n"
    "import time\n"
    "def test_outer():\n"
    f"    @pytest.mark.freeze_time({INSTANT})\n"
    "    def test_inner():\n"
    "        return time.time()\n"
    "    test_inner()\n",
    # A generator in a literal iterable reaches the loop target unconsumed.
    "import time\n"
    "def test_generator_through_loop_target():\n"
    "    clock = lambda: 0\n"
    "    for values in ((clock() for _ in (1,)),):\n"
    "        clock = time.time\n"
    "        list(values)\n",
    # A sequence nested in a dictionary value is captured as it evaluates.
    "import time\n"
    "match {'clock': (time.time,), 'other': (time := None)}:\n"
    "    case {'clock': (clock,)}:\n"
    "        clock()\n",
    # A class defined inside a function is not collected.
    "import pytest\n"
    "import time\n"
    "def test_outer():\n"
    f"    @pytest.mark.freeze_time({INSTANT})\n"
    "    class TestInner:\n"
    "        def test_read(self):\n"
    "            return time.time()\n"
    "    TestInner().test_read()\n",
    # A marked test-named method of a class pytest does not collect.
    "import pytest\n"
    "import time\n"
    "class Helper:\n"
    f"    @pytest.mark.freeze_time({INSTANT})\n"
    "    def test_read(self):\n"
    "        return time.time()\n",
    # A generator in a matched sequence reaches the capture unconsumed.
    "import time\n"
    "def test_generator_through_match_capture():\n"
    "    clock = lambda: 0\n"
    "    match ((clock() for _ in (1,)),):\n"
    "        case (values,):\n"
    "            clock = time.time\n"
    "            list(values)\n",
    # A marked collected class nested in a helper class is not collected.
    "import pytest\n"
    "import time\n"
    "class Helper:\n"
    f"    @pytest.mark.freeze_time({INSTANT})\n"
    "    class TestNested:\n"
    "        def test_read(self):\n"
    "            return time.time()\n",
    # A nested function runs under the bindings where it is called, not
    # those left by a later rebinding.
    "import time\n"
    "def test_closure_called_before_rebinding():\n"
    "    clock = time.time\n"
    "    def read():\n"
    "        return clock()\n"
    "    read()\n"
    "    clock = lambda: 0\n",
    # A coroutine's body runs at its await, not where it is created.
    "import time\n"
    "async def test_coroutine_awaited_late():\n"
    "    clock = lambda: 0\n"
    "    async def read():\n"
    "        return clock()\n"
    "    pending = read()\n"
    "    clock = time.time\n"
    "    await pending\n",
    # A defaulted positional-only parameter shifts the defaults.
    "import time\n"
    "def test_defaults_with_positional_only(a=None, /, frozen_clock=None, other=None):\n"
    "    return time.time()\n",
    # A copied function name keeps referencing the same function.
    "import time\n"
    "def test_function_alias():\n"
    "    clock = lambda: 0\n"
    "    def read():\n"
    "        return clock()\n"
    "    alias = read\n"
    "    clock = time.time\n"
    "    alias()\n"
    "    clock = lambda: 0\n",
    # A generator function's body runs at iteration, not at creation.
    "import time\n"
    "def test_generator_function_iterated_late():\n"
    "    clock = lambda: 0\n"
    "    def read():\n"
    "        yield clock()\n"
    "    values = read()\n"
    "    clock = time.time\n"
    "    list(values)\n",
    # A function unpacked into a target keeps referencing the same function.
    "import time\n"
    "def test_function_through_unpacking():\n"
    "    clock = lambda: 0\n"
    "    def read():\n"
    "        return clock()\n"
    "    alias, = (read,)\n"
    "    clock = time.time\n"
    "    alias()\n"
    "    clock = lambda: 0\n",
    # A function matched into a capture keeps referencing the same function.
    "import time\n"
    "def test_function_through_match_capture():\n"
    "    clock = lambda: 0\n"
    "    def read():\n"
    "        return clock()\n"
    "    match (read,):\n"
    "        case (alias,):\n"
    "            clock = time.time\n"
    "            alias()\n"
    "            clock = lambda: 0\n",
    # A helper called at import runs before any module marker freezes.
    "import pytest\n"
    "import time\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
    "def read():\n"
    "    return time.time()\n"
    "STAMP = read()\n",
    # A generator held directly by a dictionary value reaches the capture.
    "import time\n"
    "def test_generator_through_dictionary_value():\n"
    "    clock = lambda: 0\n"
    "    match {'values': (clock() for _ in (1,))}:\n"
    "        case {'values': values}:\n"
    "            clock = time.time\n"
    "            list(values)\n",
    # A helper called by another helper settles to the outer call's bindings.
    "import time\n"
    "def test_transitive_helpers():\n"
    "    clock = lambda: 0\n"
    "    def first():\n"
    "        return clock()\n"
    "    def second():\n"
    "        return first()\n"
    "    clock = time.time\n"
    "    second()\n"
    "    clock = lambda: 0\n",
    # An assignment expression copies a function identity too.
    "import time\n"
    "def test_function_through_walrus():\n"
    "    clock = lambda: 0\n"
    "    def read():\n"
    "        return clock()\n"
    "    (alias := read)\n"
    "    clock = time.time\n"
    "    alias()\n"
    "    clock = lambda: 0\n",
    # A coroutine created under a travel runs at its await, outside it.
    "import time\n"
    "import time_machine\n"
    "async def test_coroutine_awaited_outside_travel():\n"
    "    async def read():\n"
    "        return time.time()\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        pending = read()\n"
    "    await pending\n",
    # A caller's local does not stand in for the callee's global.
    "import time\n"
    "clock = time.time\n"
    "def test_outer():\n"
    "    clock = lambda: 0\n"
    "    read()\n"
    "def read():\n"
    "    return clock()\n",
    # A caller's closure variable does not stand in for the callee's global.
    "import time\n"
    "clock = time.time\n"
    "def read():\n"
    "    return clock()\n"
    "def test_outer():\n"
    "    clock = lambda: 0\n"
    "    def caller():\n"
    "        read()\n"
    "        clock()\n"
    "    caller()\n",
    # A coroutine reads under the bindings where it is awaited.
    "import time\n"
    "async def test_coroutine_awaited_under_a_temporary_binding():\n"
    "    clock = lambda: 0\n"
    "    async def read():\n"
    "        return clock()\n"
    "    pending = read()\n"
    "    clock = time.time\n"
    "    result = await pending\n"
    "    clock = lambda: 0\n",
    # A collected test also runs on its own, outside a frozen caller.
    "import pytest\n"
    "import time\n"
    "def test_read():\n"
    "    return time.time()\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def test_frozen():\n"
    "    test_read()\n",
    # A deferred rescan keeps nested definitions bound to their functions.
    "import time\n"
    "def test_nested_definition_in_a_rescanned_callee():\n"
    "    clock = lambda: 0\n"
    "    def middle():\n"
    "        def inner():\n"
    "            return clock()\n"
    "        return inner()\n"
    "    def caller():\n"
    "        return middle()\n"
    "    clock = time.time\n"
    "    caller()\n"
    "    clock = lambda: 0\n",
    # A caller's local does not stand in for the callee's closure variable.
    "import time\n"
    "def test_outer():\n"
    "    clock = time.time\n"
    "    def read():\n"
    "        return clock()\n"
    "    def caller():\n"
    "        clock = lambda: 0\n"
    "        return read()\n"
    "    caller()\n",
    # A parameter defaulting to a function keeps referencing it.
    "import time\n"
    "def test_function_through_a_default():\n"
    "    clock = lambda: 0\n"
    "    def read():\n"
    "        return clock()\n"
    "    def caller(alias=read):\n"
    "        return alias()\n"
    "    clock = time.time\n"
    "    caller()\n"
    "    clock = lambda: 0\n",
    # A helper runs once its arguments evaluated, so an argument that rebinds
    # a closure variable decides what the helper reads.
    "import time\n"
    "def test_argument_rebinds_a_closure_variable():\n"
    "    clock = lambda: 0\n"
    "    def read(_):\n"
    "        return clock()\n"
    "    result = read(clock := time.time)\n"
    "    clock = lambda: 0\n",
    # A class body executes whenever the helper that defines it runs.
    "import time\n"
    "def test_class_body_in_a_rescanned_callee():\n"
    "    clock = lambda: 0\n"
    "    def middle():\n"
    "        class Value:\n"
    "            stamp = clock()\n"
    "    def caller():\n"
    "        return middle()\n"
    "    clock = time.time\n"
    "    caller()\n"
    "    clock = lambda: 0\n",
    # The same holds for a comprehension target over a literal iterable.
    "import time\n"
    "def test_comprehension_target_known_elements():\n"
    "    [clock() for clock in (time.time,)]\n",
    # An attribute alias built from a conditionally rebound name keeps every
    # clock-capable alternative: ``clock.now`` over ``datetime|time`` is both
    # ``datetime.datetime.now`` and ``time.now``.
    "def test_alias_keeps_alternatives(use_datetime):\n"
    "    if use_datetime:\n"
    "        from datetime import datetime as clock\n"
    "    else:\n"
    "        import time as clock\n"
    "    read = clock.now\n"
    "    read()\n",
    "def test_date_or_datetime(use_datetime):\n"
    "    if use_datetime:\n"
    "        from datetime import datetime as clock\n"
    "    else:\n"
    "        from datetime import date as clock\n"
    "    read = clock.today\n"
    "    read()\n",
    # A zero-iteration loop keeps the target's pre-loop binding, so the read
    # after the loop is real even though the body never ran.
    "import time as clock\nfor clock in ():\n    pass\nclock.time()\n",
    # A break exits past the else with the bindings it holds at that point,
    # so the pre-loop binding stays reachable after the loop.
    "import time as clock\n"
    "for _ in [0]:\n"
    "    if stop:\n"
    "        break\n"
    "    clock = fake_clock\n"
    "else:\n"
    "    clock = fake_clock\n"
    "assert clock.time() == 0\n",
    "from datetime import datetime\n"
    "async def test_zero_iterations():\n"
    "    clock = datetime.now\n"
    "    async for clock in ():\n"
    "        pass\n"
    "    clock()\n",
    # A break exits through the ``finally`` suite, so its assignments reach
    # the statement after the loop: a clock bound there is real.
    "import time\n"
    "def test_break_through_finally():\n"
    "    clock = lambda: 0\n"
    "    for _ in values:\n"
    "        try:\n"
    "            break\n"
    "        finally:\n"
    "            clock = time.time\n"
    "    clock()\n",
    # ``except*`` builds an ``ast.TryStar``; the finalizer applies to the
    # break state just the same.
    "import time\n"
    "def test_break_through_finally_star():\n"
    "    clock = lambda: 0\n"
    "    for _ in values:\n"
    "        try:\n"
    "            break\n"
    "        except* Exception:\n"
    "            pass\n"
    "        finally:\n"
    "            clock = time.time\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A ``continue`` before the rebinding skips it on that path; if every
    # iteration continues, the loop exits with the pre-loop binding.
    "import time\n"
    "clock = time.time\n"
    "for _ in (0,):\n"
    "    if skip:\n"
    "        continue\n"
    "    clock = lambda: 0\n"
    "clock()\n",
    # A rebinding inside an exception handler does not erase the binding
    # the successful try path still holds, so the success path's binding
    # must stay reachable past the loop.
    "import time\n"
    "clock = time.time\n"
    "for _ in (0,):\n"
    "    try:\n"
    "        work()\n"
    "    except ValueError:\n"
    "        clock = lambda: 0\n"
    "clock()\n",
    "import time\n"
    "clock = time.time\n"
    "try:\n"
    "    work()\n"
    "except ValueError:\n"
    "    clock = lambda: 0\n"
    "clock()\n",
    # The raise may strike after the body has rebound the clock, so the
    # handler entry carries that binding and its read is not frozen.
    "import time\n"
    "def test_handler_after_body_rebind():\n"
    "    clock = lambda: 0\n"
    "    try:\n"
    "        clock = time.time\n"
    "        raise ValueError()\n"
    "    except ValueError:\n"
    "        clock()\n",
    # A loop with both a break and a continue still runs its else suite
    # after a final-iteration continue, so the continue's held bindings
    # reach the else's read.
    "import time\n"
    "clock = time.time\n"
    "for _ in (0,):\n"
    "    if stop:\n"
    "        clock = lambda: 0\n"
    "        break\n"
    "    elif other:\n"
    "        continue\n"
    "    clock = lambda: 0\n"
    "else:\n"
    "    clock()\n",
    # A ``continue`` exits through the ``finally`` like a ``break``: the
    # finalizer's re-rebinding reaches the continue's saved state, and the
    # statements after the try do not run on that path.
    "import time\n"
    "clock = time.time\n"
    "for _ in (0,):\n"
    "    try:\n"
    "        clock = lambda: 0\n"
    "        if skip:\n"
    "            continue\n"
    "    finally:\n"
    "        clock = time.time\n"
    "    clock = lambda: 0\n"
    "clock()\n",
    # The reporting pass binds what the silent passes skip: a definition-
    # time assignment expression inside the loop is a completed-iteration
    # exit binding.
    "import time\n"
    "def test_definition_time_walrus_in_loop(values):\n"
    "    clock = lambda: 0\n"
    "    for _ in values:\n"
    "        def helper(value=(clock := time.time)):\n"
    "            pass\n"
    "    clock()\n",
    # A finalizer's conditional break is a distinct exit even when the
    # finalizer ends in an unconditional continue.
    "import time\n"
    "clock = lambda: 0\n"
    "for _ in (0,):\n"
    "    try:\n"
    "        clock = time.time\n"
    "        break\n"
    "    finally:\n"
    "        if stop:\n"
    "            break\n"
    "        continue\n"
    "else:\n"
    "    clock = lambda: 0\n"
    "clock()\n",
    # An attribute of an object may hold the clock callable: a call through
    # the attribute reads the wall clock the same way a name call does.
    "import time\n"
    "def test_direct_attribute_alias():\n"
    "    self.stamp = time.time\n"
    "    self.stamp()\n",
    # Attributes keep the branch merge too: each branch may store a
    # different clock callable, and the call may select either one.
    "from datetime import datetime\n"
    "import time\n"
    "def test_self_attribute_alias(flag):\n"
    "    if flag:\n"
    "        clock = datetime.now\n"
    "    else:\n"
    "        clock = time.time\n"
    "    self.clock = clock\n"
    "    self.clock()\n",
    # A conditional expression stored on another object keeps both
    # alternatives reachable through the attribute read.
    "from datetime import datetime\n"
    "import time\n"
    "def test_object_attribute_alias(obj, flag):\n"
    "    alt = datetime.now if flag else time.time\n"
    "    obj.now = alt\n"
    "    obj.now()\n",
    # Reading through an attribute alias into a name keeps the union, so a
    # later call through the copy is still reported.
    "from datetime import datetime\n"
    "import time\n"
    "def test_attribute_alias_copied_to_name(flag):\n"
    "    if flag:\n"
    "        clock = datetime.now\n"
    "    else:\n"
    "        clock = time.time\n"
    "    self.clock = clock\n"
    "    reader = self.clock\n"
    "    reader()\n",
    # A branch that replaces a clock member's attribute without covering the
    # other path leaves the member itself readable: the empty record is no
    # information, and the composed member path decides.
    "import time\n"
    "def test_branch_join_keeps_member_fallback(flag):\n"
    "    if flag:\n"
    "        time.time = lambda: 0\n"
    "    time.time()\n",
    # The same holds when only the skipped branch replaces the member, even
    # with a non-clock callable: the untouched path still reads the member.
    "import time\n"
    "def test_branch_join_keeps_nonclock_record_fallback(flag):\n"
    "    if flag:\n"
    "        time.time = time.monotonic\n"
    "    time.time()\n",
    # Unpacking binds each leaf in assignment order, so a record a later
    # leaf assigns survives the holder an earlier leaf replaced.
    "import time\n"
    "from types import SimpleNamespace\n"
    "def test_unpacking_binds_in_assignment_order():\n"
    "    obj, obj.clock = SimpleNamespace(), time.time\n"
    "    obj.clock()\n",
    # A chained assignment captures the right-hand side before any target
    # rebinds, so an attribute target records that value, not a
    # re-resolution through the rebound alias.
    "import time as clock\n"
    "clock = holder.read = clock.time\n"
    "def test_chained_target_uses_captured_rhs():\n"
    "    holder.read()\n",
    # A record assigned on only one branch merges with the other branch's
    # composed fallback: the recorded path reads the wall clock and the
    # untouched member path stays resolvable.
    "import time\n"
    "def test_single_branch_record(obj, flag):\n"
    "    if flag:\n"
    "        obj.clock = time.time\n"
    "    obj.clock()\n",
    # A caller's attribute records for a holder the callee sees as a global
    # do not describe the callee's binding: the callee resolves the member
    # through the module.
    "import time as clock\n"
    "from time import monotonic as reader\n"
    "def read():\n"
    "    return clock.time()\n"
    "def test_foreign_holder():\n"
    "    clock = SimpleNamespace()\n"
    "    clock.time = reader\n"
    "    read()\n",
    # An import binds a fresh module object, so the old holder's attribute
    # records do not mask the member the import exposes.
    "import time\n"
    "from types import SimpleNamespace\n"
    "def test_import_replaces_holder():\n"
    "    clock = SimpleNamespace()\n"
    "    clock.time = time.monotonic\n"
    "    import time as clock\n"
    "    clock.time()\n",
]

#: The same reads are accepted once the clock is frozen at function, class or
#: module scope, inside ``time_machine.travel`` or through ``frozen_clock``.
FROZEN_READS = [
    # A helper's freeze is judged by its final callers, so a chain whose first
    # speculative scan ran unfrozen settles to the marker's freeze.
    "import pytest\n"
    "import time\n"
    "def read():\n"
    "    return time.time()\n"
    "def helper():\n"
    "    return read()\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def test_chain():\n"
    "    helper()\n",
    f"import pytest\nfrom datetime import datetime\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def test_marker():\n"
    "    datetime.now()\n",
    f"import pytest\nfrom datetime import datetime\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "class TestFrozen:\n"
    "    def test_marker_on_class(self):\n"
    "        datetime.now()\n",
    f"import pytest\nfrom app.db.base import utc_now\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
    "def test_pytestmark():\n"
    "    utc_now()\n",
    f"import pytest\nfrom datetime import datetime, UTC\n"
    f"pytestmark = [pytest.mark.freeze_time({INSTANT}), pytest.mark.usefixtures('db')]\n"
    "def test_pytestmark_list():\n"
    "    datetime.now(UTC)\n",
    f"import pytest\nfrom datetime import datetime\n"
    f"import time_machine\n"
    f"def test_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        datetime.now()\n",
    "from time_machine import travel as travel_to\n"
    "from app.db.base import utc_now\n"
    f"def test_travel_alias():\n"
    f"    with travel_to({INSTANT}, tick=False):\n"
    "        utc_now()\n",
    "from app.db.base import utc_now\ndef test_fixture_argument(frozen_clock):\n    utc_now()\n",
    "from app.db.base import utc_now\n"
    "import pytest\n"
    "@pytest.fixture\n"
    "def seeded(frozen_clock):\n"
    "    return utc_now()\n",
    "import time\n"
    "def test_elapsed_measures_are_not_clock_reads():\n"
    "    return time.monotonic(), time.perf_counter()\n",
    "import time\ndef test_shadowed_time():\n    time = load_time()\n    return time.time()\n",
    # Every reachable path replaces the clock binding, so nothing is reported.
    "import time as clock\n"
    "def pick(fake):\n"
    "    if fake:\n"
    "        clock = fake\n"
    "    else:\n"
    "        clock = fake_other\n"
    "    return clock.time()\n",
    # Declaration order does not matter: the marker covers functions and
    # methods regardless of where pytestmark appears.
    "from app.db.base import utc_now\n"
    "import pytest\n"
    "def test_declared_before_the_marker():\n"
    "    utc_now()\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n",
    "import pytest\n"
    "from datetime import datetime\n"
    "class TestLate:\n"
    "    def test_method(self):\n"
    "        datetime.now()\n"
    f"    pytestmark = pytest.mark.freeze_time({INSTANT})\n",
    # Context managers evaluate in entry order: an expression passed to a
    # later manager runs inside the earlier travel.
    "from contextlib import nullcontext\n"
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_manager_order():\n"
    "    with time_machine.travel(DESTINATION, tick=False), nullcontext(datetime.now()):\n"
    "        pass\n",
    # The most recently entered travel decides: a deterministic travel
    # re-freezes the entry list and the block after a ticking one.
    "from contextlib import nullcontext\n"
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_deterministic_travel_after_ticking():\n"
    f"    with time_machine.travel(DESTINATION), time_machine.travel({INSTANT}, tick=False), "
    "nullcontext(datetime.now()):\n"
    "        pass\n",
    # A false positive is as wrong as a miss: when every branch of an
    # exhaustive expression rebinds the alias, the pre-expression binding is
    # unreachable and nothing reads the wall clock.
    "import time\n"
    "def test_ifexp_both_branches_rebind(flag):\n"
    "    clock = time.time\n"
    "    value = (clock := lambda: 0) if flag else (clock := lambda: 1)\n"
    "    clock()\n",
    "import time\n"
    "def test_boolop_first_operand_rebinds(flag):\n"
    "    clock = time.time\n"
    "    (clock := lambda: 0) and flag\n"
    "    clock()\n",
    "import time\n"
    "def test_match_wildcard_covers(mode):\n"
    "    clock = time.time\n"
    "    match mode:\n"
    "        case 'fake':\n"
    "            clock = fake_clock\n"
    "        case _:\n"
    "            clock = fake_other\n"
    "    clock()\n",
    # A loop that completes always runs its else, so an else-side rebinding
    # makes the pre-loop binding unreachable.
    "import time\n"
    "def test_loop_else_rebinds():\n"
    "    clock = time.time\n"
    "    for _ in []:\n"
    "        pass\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A match capture rebinds the captured name to the matched value before
    # the case runs, so a non-clock subject's capture cannot read the clock.
    "import time\n"
    "def test_match_capture_binds_subject():\n"
    "    clock = time.time\n"
    "    match (lambda: 0):\n"
    "        case clock:\n"
    "            return clock()\n",
    # A try with an else block passes successful execution through it, so
    # an unconditional else-side rebinding covers every successful exit.
    "import time\n"
    "def test_try_else_covers_success():\n"
    "    clock = time.time\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError:\n"
    "        clock = lambda: 0\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # An ``except ... as`` target holds the exception object, not the
    # previous binding, and Python deletes it when the block exits.
    "import time\n"
    "def test_except_as_binds_the_exception():\n"
    "    clock = time.time\n"
    "    try:\n"
    "        pass\n"
    "    except ValueError as clock:\n"
    "        clock()\n",
    # Each iteration reassigns the loop target before the body runs, so a
    # body-end rebinding never reaches the next iteration's read.
    "import time\n"
    "def test_target_rebinds_each_iteration():\n"
    "    for clock in [lambda: 0, lambda: 0]:\n"
    "        clock()\n"
    "        clock = time.time\n",
    # An unconsumed generator never runs its body, so its walrus does not
    # reach the containing scope.
    "import time\n"
    "def test_unconsumed_generator_walrus():\n"
    "    clock = lambda: 0\n"
    "    unused = ((clock := time.time) for _ in (1,))\n"
    "    clock()\n",
    # A break crosses the finally block, so the finally's assignments cover
    # the break exit too.
    "import time\n"
    "def test_finally_covers_break_exit():\n"
    "    clock = time.time\n"
    "    for _ in (1,):\n"
    "        try:\n"
    "            break\n"
    "        finally:\n"
    "            clock = lambda: 0\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A continue crosses the finally block the same way.
    "import time\n"
    "def test_finally_covers_continue_exit():\n"
    "    clock = lambda: 0\n"
    "    for _ in (1, 2):\n"
    "        clock()\n"
    "        try:\n"
    "            clock = time.time\n"
    "            continue\n"
    "        finally:\n"
    "            clock = lambda: 0\n",
    # An inner loop's continue stays scoped to that loop.
    "import time\n"
    "def test_inner_continue_stays_scoped():\n"
    "    clock = lambda: 0\n"
    "    for _ in (1, 2):\n"
    "        clock()\n"
    "        for _ in (1,):\n"
    "            clock = time.time\n"
    "            continue\n"
    "        clock = lambda: 0\n",
    # A break inside a nested loop belongs to that loop, so the outer loop
    # still exhausts and its else rebinds the alias.
    "import time\n"
    "def test_nested_loop_break_stays_inner():\n"
    "    clock = time.time\n"
    "    for _ in (1,):\n"
    "        for _ in (2,):\n"
    "            break\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A generator consumed inside a travel runs under the fixed clock.
    "import time\n"
    "import time_machine\n"
    "def test_generator_consumed_in_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        list(time.time() for _ in (1,))\n",
    # Completed iterations of a broken loop always run the ``else``, so
    # the pre-else body state is no exit of its own.
    "import time\n"
    "def test_else_replaces_pre_else_state(flag):\n"
    "    clock = time.time\n"
    "    for _ in (0,):\n"
    "        if flag:\n"
    "            clock = lambda: 0\n"
    "            break\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # A finalizer's unconditional ``continue`` cancels the pending break:
    # the jump re-enters the loop and the ``else`` decides the exit.
    "import time\n"
    "def test_finally_continue_cancels_break():\n"
    "    clock = lambda: 0\n"
    "    for _ in (0,):\n"
    "        try:\n"
    "            break\n"
    "        finally:\n"
    "            clock = time.time\n"
    "            continue\n"
    "    else:\n"
    "        clock = lambda: 0\n"
    "    clock()\n",
    # An exhausted iterable always runs the else suite, so an else that
    # replaces the binding decides the post-loop state on its own.
    "import time as clock\n"
    "from types import SimpleNamespace\n"
    "for clock in ():\n"
    "    pass\n"
    "else:\n"
    "    clock = SimpleNamespace(time=lambda: 0)\n"
    "assert clock.time() == 0\n",
    # A final-iteration ``continue`` still runs the loop's ``else`` suite,
    # so an else that replaces the binding decides the post-loop state; the
    # raw continue state is not a post-loop exit of its own.
    "import time\n"
    "clock = time.time\n"
    "for _ in (0,):\n"
    "    continue\n"
    "else:\n"
    "    clock = lambda: 0\n"
    "clock()\n",
    # The zero-iteration path runs through the ``else`` suite, so an else
    # that replaces the binding decides the state after the loop.
    "import time as clock\n"
    "from types import SimpleNamespace\n"
    "for clock in ():\n"
    "    pass\n"
    "else:\n"
    "    clock = SimpleNamespace(time=lambda: 0)\n"
    "clock.time()\n",
    # Without a break the else suite runs on every normal exit, so it also
    # decides the post-loop state when the iterable is not provably empty:
    # a binding it replaces cannot reach the statement after the loop.
    "import time as clock\n"
    "from types import SimpleNamespace\n"
    "def test_else_replaces_shadowed():\n"
    "    for clock in values:\n"
    "        pass\n"
    "    else:\n"
    "        clock = SimpleNamespace(time=lambda: 0)\n"
    "    clock.time()\n",
    # An attribute that holds a clock callable is covered by the test's
    # freeze like any other read.
    "import pytest\n"
    "import time\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def test_frozen_attribute_alias():\n"
    "    self.stamp = time.time\n"
    "    self.stamp()\n",
]


def test_the_determinism_allowlist_is_current() -> None:
    assert guard.main([]) == 0


@pytest.mark.parametrize("source", UNFROZEN_READS)
def test_the_guard_rejects_unfrozen_clock_reads(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1


@pytest.mark.parametrize("source", FROZEN_READS)
def test_the_guard_accepts_frozen_clock_reads(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 0
    assert guard.main(["--root", str(tmp_path), "--strict"]) == 0


def test_an_attribute_alias_to_a_non_clock_callable_stays_accepted() -> None:
    """Storing and calling a fake through an attribute records no clock read."""
    source = "def test_fake_attribute_alias():\n    self.clock = lambda: 0\n    self.clock()\n"
    assert guard.clock_findings(source).reads == []


def test_an_untracked_attribute_read_stays_accepted() -> None:
    """An attribute no assignment in the scope recorded is not a clock read."""
    source = "def test_unknown_attribute(fake):\n    return fake.now()\n"
    assert guard.clock_findings(source).reads == []


def test_rebinding_the_holder_clears_its_attribute_aliases() -> None:
    """A new object replaces the attribute the old one held."""
    source = (
        "import time\n"
        "def test_replaced_holder():\n"
        "    obj.now = time.time\n"
        "    obj = Fake()\n"
        "    obj.now()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_unpacking_rebind_clears_holder_attributes() -> None:
    """A tuple leaf that rebinds the holder clears the attributes it held."""
    source = (
        "import time\n"
        "from types import SimpleNamespace\n"
        "def test_unpacking_replacement():\n"
        "    obj.clock = time.time\n"
        "    obj, = (SimpleNamespace(clock=lambda: 0),)\n"
        "    obj.clock()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_replaced_attribute_path_clears_its_descendants() -> None:
    """Replacing a nested holder clears the deeper attributes it held."""
    source = (
        "import time\n"
        "from types import SimpleNamespace\n"
        "def test_nested_holder_replacement():\n"
        "    obj.child.now = time.time\n"
        "    obj.child = SimpleNamespace(now=lambda: 0)\n"
        "    obj.child.now()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_unpacking_an_attribute_leaf_clears_its_descendants() -> None:
    """An unpacking leaf that is itself an attribute replaces that object."""
    source = (
        "import time\n"
        "from types import SimpleNamespace\n"
        "def test_unpacking_attribute_leaf():\n"
        "    obj.child.clock = time.time\n"
        "    obj.child, = (SimpleNamespace(clock=lambda: 0),)\n"
        "    obj.child.clock()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_a_local_shadow_drops_the_holder_inherited_records() -> None:
    """A parameter shadowing a module holder does not inherit its records."""
    source = (
        "import time\n"
        "from types import SimpleNamespace\n"
        "obj.clock = time.time\n"
        "def test_shadowed_holder(obj=SimpleNamespace(clock=lambda: 0)):\n"
        "    obj.clock()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_postponed_annotation_scopes_do_not_crash_the_guard() -> None:
    """A lambda inside a postponed annotation has no symtable scope to find."""
    source = (
        "from __future__ import annotations\n"
        "from typing import Annotated\n"
        "def helper(value: Annotated[int, lambda x: x]) -> None:\n"
        "    pass\n"
    )
    assert guard.clock_findings(source).reads == []


def test_generic_type_parameter_scopes_do_not_crash_the_guard() -> None:
    """A lambda in a generic function's annotation lives in the type-parameter scope."""
    source = (
        "from typing import Annotated\n"
        "def helper[T](value: Annotated[T, lambda: None]) -> T:\n"
        "    ...\n"
    )
    assert guard.clock_findings(source).reads == []


def test_nested_type_alias_scopes_do_not_crash_the_guard() -> None:
    """A lambda in a type-alias value sits below two wrapper scopes."""
    source = "from typing import Annotated\ntype Alias[T] = Annotated[T, lambda: None]\n"
    assert guard.clock_findings(source).reads == []


def test_same_line_definitions_resolve_to_distinct_tables() -> None:
    """Two lambdas in one annotation share a line and must not share a table."""
    source = (
        "from typing import Annotated\n"
        "def helper[T](x: Annotated[T, lambda: None, lambda: lambda: None]) -> T:\n"
        "    ...\n"
    )
    assert guard.clock_findings(source).reads == []


def test_comprehension_walrus_binds_in_the_containing_scope() -> None:
    """A walrus inside a comprehension updates the enclosing scope's aliases."""
    source = (
        "import time\n"
        "def test_walrus_leak():\n"
        "    stamps = [(clock := time.time) for _ in (1,)]\n"
        "    return clock()\n"
    )
    assert guard.clock_findings(source).reads != []


def test_while_condition_rescan_leaves_definitions_alone() -> None:
    """Re-evaluating a while condition must not consume its scopes twice."""
    source = "def test_generator_condition():\n    while any(x for x in []):\n        pass\n"
    assert guard.clock_findings(source).reads == []


def test_type_parameter_bounds_do_not_confuse_annotation_lookups() -> None:
    """A bound lambda and an annotation lambda share a line but not a scope."""
    source = "def helper[T: (lambda: None)](x: (lambda: lambda: None)): pass\n"
    assert guard.clock_findings(source).reads == []


def test_generic_defaults_resolve_in_the_enclosing_scope() -> None:
    """A default expression of a generic function is not inside the wrapper scope."""
    source = "def helper[T](value=lambda: None): pass\n"
    assert guard.clock_findings(source).reads == []


def test_function_local_annotations_are_not_evaluated() -> None:
    """Python never evaluates a function-local variable annotation."""
    source = (
        "import time\n"
        "from typing import Annotated\n"
        "def test_local_annotation():\n"
        "    value: Annotated[int, time.time()] = 1\n"
    )
    assert guard.clock_findings(source).reads == []


def test_vararg_annotation_visits_in_declaration_order() -> None:
    """The vararg annotation evaluates before the keyword-only ones."""
    source = (
        "from typing import Annotated\n"
        "def helper(*args: Annotated[int, lambda: None],\n"
        "           value: Annotated[int, lambda: lambda: None]): pass\n"
    )
    assert guard.clock_findings(source).reads == []


def test_dict_scopes_preassign_in_symbol_table_order() -> None:
    """Dictionary-key scopes precede value scopes in the symbol table."""
    source = "mapping = {0: lambda: lambda: None, (lambda: None): 1}\n"
    assert guard.clock_findings(source).reads == []


def test_lambda_default_precedes_the_lambda_scope() -> None:
    """A lambda default's table precedes the lambda's own in its scope."""
    source = "mapping = {0: lambda x=(lambda: None): lambda: None}\n"
    assert guard.clock_findings(source).reads == []


def test_generator_iterable_precedes_the_generator_scope() -> None:
    """A generator's first iterable's table precedes the generator's own."""
    source = "mapping = {0: ((lambda: lambda: None) for x in (y for y in []))}\n"
    assert guard.clock_findings(source).reads == []


def test_eager_comprehension_scopes_preassign_in_order() -> None:
    """An eager comprehension inlines with its iterables' tables first."""
    source = "mapping = {0: [lambda: lambda: None for x in (lambda: None,)]}\n"
    assert guard.clock_findings(source).reads == []


def test_dict_comprehension_value_scope_precedes_the_key() -> None:
    """A dictionary comprehension's value scope precedes its key scope."""
    source = "mapping = {(lambda: lambda: None): (lambda: None) for _ in []}\n"
    assert guard.clock_findings(source).reads == []


def test_dict_comprehension_filters_precede_the_entry_scopes() -> None:
    """A dictionary comprehension's filter scopes precede its entry scopes."""
    source = "mapping = {(lambda: lambda: None): (lambda: None) for _ in [] if (lambda: None)}\n"
    assert guard.clock_findings(source).reads == []


def test_dict_comprehension_later_iterables_precede_the_entry_scopes() -> None:
    """A dictionary comprehension's later iterable scopes precede its entries."""
    source = (
        "mapping = {(lambda: lambda: None): (lambda: None) for _ in [] for y in (lambda: None,)}\n"
    )
    assert guard.clock_findings(source).reads == []


def test_comprehension_target_scopes_precede_the_entry_scopes() -> None:
    """A comprehension target's expression scopes precede the entries'."""
    for source in (
        "mapping = {(lambda: lambda: None): (lambda: None) for obj[lambda: None] in []}\n",
        "values = [(lambda: lambda: None) for obj[lambda: None] in []]\n",
        "mapping = {(lambda: lambda: None): 1 for _ in [] for obj[lambda: None] in []}\n",
    ):
        assert guard.clock_findings(source).reads == []


def test_for_target_scopes_are_consumed_once() -> None:
    """A statement-level loop target's expression scopes are consumed once."""
    source = "obj = {}\nfor obj[lambda: None] in (1,):\n    pass\n"
    assert guard.clock_findings(source).reads == []


def test_later_level_target_scopes_precede_the_iterable_scopes() -> None:
    """A later generator level lists its target scope before its iterable's."""
    for source in (
        "[0 for x in [] for obj[lambda: lambda: None] in (lambda: None,)]\n",
        "{0 for x in [] for obj[lambda: lambda: None] in (lambda: None,)}\n",
        "values = ((lambda: lambda: None) for x in []"
        " for obj[lambda: lambda: None] in (lambda: None,))\nlist(values)\n",
    ):
        assert guard.clock_findings(source).reads == []


def test_conftest_module_marker_does_not_freeze_fixtures() -> None:
    """A conftest module's marks never reach the tests using its fixtures."""
    source = (
        "import pytest\n"
        "import time\n"
        f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
        "@pytest.fixture\n"
        "def stamp():\n"
        "    return time.time()\n"
    )
    assert guard.clock_findings(source).reads == []
    assert guard.clock_findings(source, module_marker_applies=False).reads != []


def test_for_target_scopes_precede_the_iterable_scopes() -> None:
    """A loop target lists its scopes before the iterable's."""
    source = "obj = {}\nfor obj[lambda: lambda: None] in (lambda: None,):\n    pass\n"
    assert guard.clock_findings(source).reads == []


def test_filters_rerun_before_each_comprehension_element() -> None:
    """Each iteration reruns the filters before its element reads."""
    for source in (
        "import time\n"
        "clock = lambda: 0\n"
        "[(clock(), (clock := time.time)) for _ in (1, 2) if (clock := lambda: 0)]\n",
        "import time\n"
        "def test_generator():\n"
        "    clock = lambda: 0\n"
        "    values = ((clock(), (clock := time.time)) for _ in (1, 2)"
        " if (clock := lambda: 0))\n"
        "    list(values)\n",
    ):
        assert guard.clock_findings(source).reads == []


def test_statement_target_scopes_precede_the_value_scopes() -> None:
    """A statement lists its target scopes before its value's."""
    for source in (
        "obj = {}\nobj[lambda: lambda: None] = lambda: None\n",
        "obj = {}\nobj[lambda: lambda: None] += lambda: None\n",
        "obj = {}\nobj[lambda: lambda: None]: (lambda: None) = lambda: None\n",
        "obj = {}\nobj[lambda: lambda: None] = obj[lambda: None] = lambda: None\n",
    ):
        assert guard.clock_findings(source).reads == []


def test_annotated_conftest_marker_does_not_freeze_fixtures() -> None:
    """Annotating ``pytestmark`` does not make a conftest marker apply."""
    source = (
        "import pytest\n"
        "import time\n"
        f"pytestmark: object = pytest.mark.freeze_time({INSTANT})\n"
        "@pytest.fixture\n"
        "def stamp():\n"
        "    return time.time()\n"
    )
    assert guard.clock_findings(source, module_marker_applies=False).reads != []


def test_rebinding_discards_the_pending_generator() -> None:
    """A rebound generator variable no longer carries its assignments."""
    source = (
        "import time\n"
        "def test_discarded():\n"
        "    clock = lambda: 0\n"
        "    values = ((clock := time.time) for _ in (1,))\n"
        "    values = []\n"
        "    list(values)\n"
        "    clock()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_keyword_annotation_scopes_follow_the_symbol_table() -> None:
    """The ``**kwargs`` annotation's scope precedes the keyword-only ones."""
    source = (
        "from typing import Annotated\n"
        "def f(*, k: Annotated[int, (lambda: lambda: None)],"
        " **kw: Annotated[int, (lambda: None)]):\n"
        "    pass\n"
    )
    assert guard.clock_findings(source).reads == []


def test_the_final_pytestmark_assignment_decides() -> None:
    """A replaced module marker no longer freezes the module."""
    source = (
        "import pytest\n"
        "import time\n"
        f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
        "pytestmark = []\n"
        "def test_read():\n"
        "    time.time()\n"
    )
    assert guard.clock_findings(source).reads != []


def test_annotated_generator_waits_for_its_consumer() -> None:
    """An annotated generator variable keeps its assignments until consumed."""
    source = (
        "import time\n"
        "def test_unconsumed():\n"
        "    clock = lambda: 0\n"
        "    values: object = ((clock := time.time) for _ in (1,))\n"
        "    clock()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_generator_body_reads_under_the_bindings_at_consumption() -> None:
    """A generator consumed after a rebinding reads the replacement."""
    source = (
        "import time\n"
        "def test_consumed_late():\n"
        "    clock = time.time\n"
        "    values = (clock() for _ in (1,))\n"
        "    clock = lambda: 0\n"
        "    list(values)\n"
    )
    assert guard.clock_findings(source).reads == []


def test_indirect_parametrization_keeps_the_fixture() -> None:
    """An indirect parametrization still requests the freezing fixture."""
    source = (
        "import pytest\n"
        "import time\n"
        "@pytest.mark.parametrize('frozen_clock', [None], indirect=['frozen_clock'])\n"
        "def test_indirect(frozen_clock):\n"
        "    return time.time()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_closure_called_after_rebinding_reads_the_replacement() -> None:
    """A nested function called after a rebinding reads the replacement."""
    source = (
        "import time\n"
        "def test_closure_called_late():\n"
        "    clock = time.time\n"
        "    def read():\n"
        "        return clock()\n"
        "    clock = lambda: 0\n"
        "    read()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_a_helper_defined_before_its_frozen_caller_is_accepted() -> None:
    """A rescan replaces the reads a helper's first scan found."""
    source = (
        "import pytest\n"
        "import time\n"
        "def read():\n"
        "    return time.time()\n"
        f"@pytest.mark.freeze_time({INSTANT})\n"
        "def test_read():\n"
        "    read()\n"
    )
    assert guard.clock_findings(source).reads == []


def test_annotated_assignment_scopes_preassign_in_order() -> None:
    """An annotated assignment's annotation scopes precede the value's."""
    source = "stamp: (lambda: None) = lambda: lambda: None\n"
    assert guard.clock_findings(source).reads == []


def test_postponed_annotations_have_no_scope() -> None:
    """A postponed annotation creates no scope to preassign."""
    source = (
        "from __future__ import annotations\n"
        "from typing import Annotated\n"
        "stamp: Annotated[int, lambda: None] = 0\n"
    )
    assert guard.clock_findings(source).reads == []


def test_keyword_arguments_preassign_in_order() -> None:
    """A call's keyword arguments keep their own scopes."""
    source = "mapping = {0: f(x=lambda: lambda: None) or (lambda: None)}\n"
    assert guard.clock_findings(source).reads == []


def test_generic_class_bases_resolve_inside_the_wrapper_scope() -> None:
    """A generic class's bases are evaluated inside its type-parameter scope."""
    source = "class Base: pass\nclass Helper[T: (lambda: None)]((lambda: lambda: Base)()()): pass\n"
    assert guard.clock_findings(source).reads == []


def test_the_guard_scans_support_and_module_level_reads(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    support = tests / "support"
    support.mkdir(parents=True)
    (support / "clocks.py").write_text("from datetime import datetime\nNOW = datetime.now()\n")
    (tests / "conftest.py").write_text("import time\nSTART = time.time()\n")
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1


def test_growth_fails_stale_fails_and_lowering_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    module = tests / "test_example.py"
    source = "from datetime import datetime\ndef test_now():\n    datetime.now()\n"
    module.write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps(guard.inventory(tmp_path)))
    args = ["--root", str(tmp_path), "--allowlist", str(allowlist)]
    assert guard.main(args) == 0
    assert "WARNING: 1 unfrozen clock read" in capsys.readouterr().out
    module.write_text(source + "datetime.now()\n")
    assert guard.main(args) == 1
    module.write_text("import time\ndef test_later():\n    time.monotonic()\n")
    assert guard.main(args) == 1  # stale entry, the read is gone


def test_strict_rejects_even_allowlisted_reads(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "from app.db.base import utc_now\ndef test_now():\n    utc_now()\n"
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps(guard.inventory(tmp_path)))
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 0
    assert guard.main(["--root", str(tmp_path), "--strict"]) == 1


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("x()  # noqa: AQPY001", 1),
        ("x()  # noqa: AQPY001, PLR0913", 1),
        ("x()  # noqa: TID251", 1),
        ("x()  # noqa: TID251 - deliberate stall past the timeout (#847)", 0),
        ("x()  # noqa: AQPY001 - frozen by the class marker (#848)", 0),
        ("x()  # noqa: PLR0913 - wide fixture set (#849)", 0),
        ('"""# noqa: AQPY001"""\nx()', 0),  # docstrings are not comments
    ],
)
def test_aqpy000_counts_only_unreasoned_guarded_suppressions(
    tmp_path: Path, comment: str, expected: int
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(comment + "\n")
    inventory = guard.inventory(tmp_path)
    assert inventory == ({"tests/test_example.py": {guard.AQPY000: expected}} if expected else {})


def test_a_valid_suppression_excludes_the_read_but_a_bare_one_does_not(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "from app.db.base import utc_now\n"
        "def test_reasoned():\n"
        "    utc_now()  # noqa: AQPY001 - compares against a service-side clock (#847)\n"
        "def test_bare():\n"
        "    utc_now()  # noqa: AQPY001\n"
    )
    inventory = guard.inventory(tmp_path)
    assert inventory == {"tests/test_example.py": {guard.AQPY001: 1, guard.AQPY000: 1}}


def test_the_ruff_config_declares_the_aqpy_prefix() -> None:
    config: dict[str, Any] = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert "AQPY" in config["tool"]["ruff"]["lint"]["external"]


def test_the_lint_task_runs_the_guard() -> None:
    tasks: dict[str, Any] = tomllib.loads((BACKEND / "mise.toml").read_text())["tasks"]
    lint = tasks["risk-service:lint"]["run"]
    assert "uv run ruff check ." in lint
    assert "uv run python scripts/lint_baseline.py check" in lint
    assert "uv run python scripts/test_determinism_guard.py" in lint


def test_the_root_lint_task_delegates_to_the_backend() -> None:
    tasks: dict[str, Any] = tomllib.loads((BACKEND / "../mise.toml").read_text())["tasks"]
    root = tasks["risk-service:lint"]
    assert root["run"] == "mise run risk-service:lint"
    assert root["dir"] == "backend"


@pytest.mark.parametrize(
    "source",
    [
        dedent(
            f"""
            import pytest
            from app.db.base import utc_now
            @pytest.mark.freeze_time({INSTANT})
            def test_frozen():
                utc_now()
            def test_unfrozen():
                utc_now()
            """
        ),
        dedent(
            """
            from datetime import datetime
            import time_machine
            def test_mixed():
                with time_machine.travel(START, tick=False):
                    datetime.now()
                datetime.now()
            """
        ),
    ],
)
def test_only_the_unfrozen_part_of_a_file_is_counted(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1


def test_distinct_calls_on_one_line_each_count() -> None:
    """Same-line reads are one per call site, not one per line."""
    source = (
        "from datetime import datetime\n"
        "def test_two_on_one_line():\n"
        "    a = datetime.now(); b = datetime.now()\n"
    )
    assert len(guard.clock_findings(source).reads) == 2


def test_break_inside_finally_is_not_replayed_over_itself() -> None:
    """A break originating in the finalizer already carries its assignments."""
    source = (
        "import time\n"
        "clock = lambda: 0\n"
        "replacement = time.time\n"
        "for _ in range(1):\n"
        "    try:\n"
        "        pass\n"
        "    finally:\n"
        "        clock = replacement\n"
        "        replacement = lambda: 0\n"
        "        break\n"
        "else:\n"
        "    clock = lambda: 0\n"
        "clock()\n"
    )
    assert guard.clock_findings(source).reads != []


def test_finalizer_replay_records_one_read_per_call_site() -> None:
    """A call site resolved differently per path still counts once."""
    source = (
        "import time\n"
        "from datetime import datetime\n"
        "clock = time.time\n"
        "for _ in range(3):\n"
        "    try:\n"
        "        if flag:\n"
        "            break\n"
        "        clock = datetime.now\n"
        "    finally:\n"
        "        clock()\n"
    )
    assert len(guard.clock_findings(source).reads) == 1


def test_definition_default_in_a_jumping_finalizer_is_a_known_over_report() -> None:
    """A replayed finalizer skips definition defaults, so this read is reported."""
    source = (
        "import time\n"
        "clock = time.time\n"
        "for _ in (0,):\n"
        "    try:\n"
        "        continue\n"
        "    finally:\n"
        "        def helper(value=(clock := lambda: 0)):\n"
        "            pass\n"
        "        break\n"
        "else:\n"
        "    clock = lambda: 0\n"
        "clock()\n"
    )
    assert guard.clock_findings(source).reads != []
