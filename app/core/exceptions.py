class OperationAlreadyExistsError(Exception):
    """Raised when an operation with the given ID already exists."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id

        super().__init__(
            f"Operation '{operation_id}' already exists"
        )


class OperationNotFoundError(Exception):
    """Raised when an operation with the given ID does not exist."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id

        super().__init__(
            f"Operation '{operation_id}' not found"
        )
