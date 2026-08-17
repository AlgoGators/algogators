import asyncio
import logging
import os

from data_ngin.application.orchestrator import Orchestrator
from data_ngin.utils.dynamic_loader import DEFAULT_CONFIG_PATH, load_config


def main() -> None:
    """
    Main entry point for the data pipeline.

    TO-DO:
    - Create file for interacting with database and pulling data (look into ORMs)
    - Change OHLCV class to to handle more than just _1d
    - Dockerize and implement Airflow
    """
    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        # Load configuration
        file_path = os.path.dirname(DEFAULT_CONFIG_PATH)
        for filename in os.listdir(file_path):
            file_path = os.path.join(file_path, filename)
            config_path = DEFAULT_CONFIG_PATH
            logging.info(f"Loading configuration from {config_path}")
            config = load_config(config_path)

            # Initialize the Orchestrator
            logging.info("Initializing orchestrator...")
            orchestrator = Orchestrator(config=config)

            # Run the pipeline
            logging.info("Starting the data pipeline...")
            asyncio.run(orchestrator.run())

            logging.info("Pipeline execution completed successfully.")

    except Exception as e:
        logging.error(f"Pipeline execution failed: {e}")


if __name__ == "__main__":
    main()
