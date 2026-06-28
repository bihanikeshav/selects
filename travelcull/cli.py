"""travelcull CLI entry point."""
import click


@click.group()
def main() -> None:
    """travelcull – local AI-assisted travel photo and video culling."""
