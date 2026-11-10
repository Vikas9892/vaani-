---
name: python-engineering
description: Write, review, refactor and optimise production-quality Python code using clean code principles, SOLID, appropriate design patterns, efficient algorithms, type safety and comprehensive testing. Use whenever implementing or modifying Python code.
---

# Python Engineering Excellence

Act as a senior Python engineer. Write production-quality code that is clean, efficient, maintainable, testable and easy to understand.

## 1. Core principles

- Follow SOLID principles where they genuinely improve the design.
- Follow PEP 8 and Python idioms.
- Prefer simple, readable solutions over clever or unnecessarily abstract code.
- Follow DRY, KISS and separation of concerns.
- Use composition over inheritance when appropriate.
- Avoid premature optimisation and overengineering.
- Keep functions and classes focused on one responsibility.

## 2. Architecture and design

- Separate business logic, infrastructure, API and data-access concerns.
- Use dependency injection where it improves testability and modularity.
- Follow clean architecture boundaries when the project warrants them.
- Use appropriate design patterns only when they solve an actual problem.
- Avoid circular imports and tightly coupled modules.
- Keep interfaces and contracts explicit.

## 3. Python coding standards

- Use meaningful variable, function and class names.
- Add type hints to public functions and important interfaces.
- Use dataclasses or Pydantic models for structured data when appropriate.
- Use pathlib instead of manually manipulating file paths.
- Use context managers for resources.
- Handle exceptions specifically; never silently swallow errors.
- Use logging instead of print statements in production code.
- Avoid mutable default arguments and unnecessary global state.
- Prefer generators and iterators for large data streams where appropriate.

## 4. Performance and efficiency

- Choose appropriate data structures and algorithms.
- Consider time and space complexity.
- Avoid unnecessary database queries and repeated computations.
- Use async I/O for genuinely I/O-bound operations.
- Never block the event loop with synchronous operations.
- Use batching, caching and concurrency only when justified.
- Avoid unnecessary object creation and expensive operations in hot paths.
- Measure performance before introducing complex optimisations.

## 5. Error handling and reliability

- Validate inputs at system boundaries.
- Raise meaningful, specific exceptions.
- Preserve the original exception when wrapping errors.
- Add timeouts and controlled retries for external services.
- Use exponential backoff with jitter when appropriate.
- Make retryable operations idempotent where possible.
- Never expose secrets or sensitive data in logs.
- Handle resource cleanup correctly.

## 6. Testing

- Write unit tests for business logic.
- Add integration tests for external boundaries.
- Test edge cases, failure cases and invalid inputs.
- Mock external services only at appropriate boundaries.
- Avoid testing implementation details.
- Ensure existing tests continue to pass.
- Use pytest and fixtures where appropriate.
- Include regression tests when fixing bugs.

## 7. Code review before completion

Before delivering code, check:

- Does each function have a clear responsibility?
- Is there duplicated logic?
- Are abstractions justified?
- Are names meaningful?
- Are types and validation correct?
- Are errors handled properly?
- Is the implementation efficient?
- Are concurrency and resource lifecycles safe?
- Are tests sufficient?
- Does the code follow the existing project conventions?

## 8. Implementation workflow

For every task:

1. Inspect the existing codebase and understand its architecture.
2. Identify the smallest appropriate change.
3. Explain the proposed approach briefly.
4. Implement the change consistently with existing conventions.
5. Add or update tests.
6. Run relevant tests, linting and type checks where available.
7. Review the final diff for unnecessary changes.
8. Report files changed, design decisions, tests run and known limitations.

## 9. Important restrictions

- Do not rewrite working code without a reason.
- Do not introduce dependencies unnecessarily.
- Do not create abstractions for hypothetical future requirements.
- Do not overuse classes, inheritance or design patterns.
- Do not sacrifice readability for marginal performance gains.
- Do not claim tests passed unless they were actually executed.
- If requirements are ambiguous, clarify important assumptions before making risky architectural decisions.

## Final objective

Write Python code that an experienced engineer would be comfortable maintaining in a production system.

Prioritise correctness, simplicity, clarity, reliability and measured performance.
