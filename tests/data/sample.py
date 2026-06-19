"""Sample Python module for testing code parsing."""



class DataProcessor:
    """A data processing pipeline that transforms raw input.

    Supports multiple processing stages and configurable output formats.
    """

    def __init__(self, config_path: str | None = None):
        """Initialize the processor with optional config."""
        self.config_path = config_path or "~/.dataproc/config.yaml"
        self.stages: list[str] = []

    def add_stage(self, stage_name: str) -> None:
        """Add a processing stage to the pipeline.

        Args:
            stage_name: Name of the stage to add.
        """
        self.stages.append(stage_name)

    def process(self, data: dict) -> dict:
        """Run the full processing pipeline.

        Args:
            data: Raw input data dictionary.

        Returns:
            Processed output dictionary.
        """
        result = data
        for stage in self.stages:
            result = self._run_stage(stage, result)
        return result

    def _run_stage(self, stage: str, data: dict) -> dict:
        """Execute a single processing stage."""
        # Implementation details omitted
        return data


def create_processor(config_path: str | None = None) -> DataProcessor:
    """Factory function for creating a DataProcessor instance."""
    return DataProcessor(config_path)
