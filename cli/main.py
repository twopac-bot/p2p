"""
Command-line interface for P2P file sharing system.

Provides commands:
- upload: Share a file
- download: Download a file by ID  
- status: Check download progress
- tracker: Start the tracker server
- seed: Resume seeding a file

Uses click for CLI parsing and rich for progress bars.
"""

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    DownloadColumn,
    TransferSpeedColumn
)
from rich.table import Table
from rich.panel import Panel

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import Config
from utils.logger import setup_logging, get_logger
from peer.peer_node import PeerNode, DownloadProgress
from tracker.tracker_server import run_tracker

logger = get_logger("cli")
console = Console()


def get_config_from_options(ctx) -> Config:
    """Build Config from CLI options."""
    return Config(
        tracker_host=ctx.obj.get('tracker_host', '127.0.0.1'),
        tracker_port=ctx.obj.get('tracker_port', 8000),
        peer_port=ctx.obj.get('peer_port', 8001),
        uploads_dir=ctx.obj.get('uploads_dir', './uploads'),
        download_dir=ctx.obj.get('download_dir', './downloads'),
        chunks_dir=ctx.obj.get('chunks_dir', './chunks')
    )


@click.group()
@click.option('--tracker-host', default='127.0.0.1', help='Tracker server host')
@click.option('--tracker-port', default=8000, type=int, help='Tracker server port')
@click.option('--peer-port', default=8001, type=int, help='Local peer port')
@click.option('--uploads-dir', default='./uploads', help='Uploads directory')
@click.option('--download-dir', default='./downloads', help='Download directory')
@click.option('--chunks-dir', default='./chunks', help='Chunks directory')
@click.option('--debug', is_flag=True, help='Enable debug logging')
@click.pass_context
def cli(ctx, tracker_host, tracker_port, peer_port, uploads_dir, download_dir, chunks_dir, debug):
    """P2P File Sharing System
    
    Share and download files using peer-to-peer technology.
    
    Quick Start:
        1. Put files in 'uploads' folder
        2. Run: python -m cli.main tracker (Terminal 1)
        3. Run: python -m cli.main upload --name yourfile.zip (Terminal 2)
        4. Share the File ID with others
    """
    ctx.ensure_object(dict)
    ctx.obj['tracker_host'] = tracker_host
    ctx.obj['tracker_port'] = tracker_port
    ctx.obj['peer_port'] = peer_port
    ctx.obj['uploads_dir'] = uploads_dir
    ctx.obj['download_dir'] = download_dir
    ctx.obj['chunks_dir'] = chunks_dir
    
    import logging
    setup_logging(level=logging.DEBUG if debug else logging.INFO)


@cli.command()
@click.option('--host', default='127.0.0.1', help='Host to bind to')
@click.option('--port', default=8000, type=int, help='Port to bind to')
@click.pass_context
def tracker(ctx, host, port):
    """Start the tracker server.
    
    The tracker maintains peer-to-chunk mappings for file sharing.
    Run this before uploading or downloading files.
    
    Example:
        python -m cli.main tracker --host 0.0.0.0 --port 8000
    """
    config = Config(tracker_host=host, tracker_port=port)
    
    # Ensure directories exist
    config.ensure_directories()
    
    console.print(Panel.fit(
        f"[bold green]Starting Tracker Server[/bold green]\n"
        f"Host: {host}\n"
        f"Port: {port}\n"
        f"Press Ctrl+C to stop",
        title="P2P Tracker"
    ))
    
    try:
        asyncio.run(run_tracker(config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Tracker stopped[/yellow]")


@cli.command()
@click.option('--file', '-f', 'filepath', required=False, type=click.Path(exists=True), 
              help='Path to file to share (or use --name for file in uploads folder)')
@click.option('--name', '-n', 'filename', required=False, help='Filename in uploads folder')
@click.option('--host', default=None, help='Host to advertise to tracker')
@click.pass_context
def upload(ctx, filepath, filename, host):
    """Upload a file to share with the network.
    
    Splits the file into chunks, registers with the tracker,
    and starts serving chunks to other peers.
    
    Examples:
        python -m cli.main upload --file C:\\path\\to\\movie.mp4
        python -m cli.main upload --name movie.mp4  (from uploads folder)
    """
    config = get_config_from_options(ctx)
    config.ensure_directories()
    
    # Determine file path
    if filepath:
        filepath = Path(filepath)
    elif filename:
        filepath = Path(config.uploads_dir) / filename
        if not filepath.exists():
            console.print(f"[bold red]File not found:[/bold red] {filepath}")
            console.print(f"[dim]Put files in the 'uploads' folder: {config.uploads_dir}[/dim]")
            sys.exit(1)
    else:
        console.print("[bold red]Error:[/bold red] Provide --file or --name")
        console.print("[dim]Use --file for full path, or --name for file in uploads folder[/dim]")
        sys.exit(1)
    console.print(f"[bold]Uploading:[/bold] {filepath.name}")
    console.print(f"[dim]Size: {filepath.stat().st_size:,} bytes[/dim]")
    
    async def do_upload():
        node = PeerNode(config)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Splitting file...", total=None)
            
            async def on_progress(current: int, total: int):
                progress.update(task, completed=current, total=total, 
                              description=f"Processing chunks...")
            
            result = await node.upload(
                str(filepath),
                peer_host=host or config.tracker_host,
                peer_port=config.peer_port,
                on_progress=on_progress
            )
        
        if result.success:
            console.print()
            console.print(Panel.fit(
                f"[bold green]Upload Successful![/bold green]\n\n"
                f"[bold]File ID:[/bold] {result.file_id}\n"
                f"[bold]Filename:[/bold] {result.filename}\n"
                f"[bold]Chunks:[/bold] {result.total_chunks}\n"
                f"[bold]Serving on port:[/bold] {result.peer_port}\n\n"
                f"[dim]Share the File ID with others to let them download.[/dim]\n"
                f"[dim]Keep this process running to seed the file.[/dim]",
                title="Upload Complete"
            ))
            
            console.print("\n[yellow]Press Ctrl+C to stop seeding[/yellow]")
            
            # Keep running to seed
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                await node.stop_all()
                console.print("\n[yellow]Stopped seeding[/yellow]")
        else:
            console.print(f"[bold red]Upload failed:[/bold red] {result.error}")
            sys.exit(1)
    
    try:
        asyncio.run(do_upload())
    except KeyboardInterrupt:
        console.print("\n[yellow]Upload cancelled[/yellow]")


@cli.command()
@click.option('--id', '-i', 'file_id', required=True, help='File ID to download')
@click.option('--output', '-o', default=None, help='Output directory')
@click.pass_context
def download(ctx, file_id, output):
    """Download a file from the network.
    
    Fetches chunks from multiple peers in parallel and
    reassembles the original file.
    
    Example:
        python -m cli.main download --id abc123...
    """
    config = get_config_from_options(ctx)
    if output:
        config = Config(
            tracker_host=config.tracker_host,
            tracker_port=config.tracker_port,
            peer_port=config.peer_port,
            download_dir=output,
            chunks_dir=config.chunks_dir
        )
    
    console.print(f"[bold]Downloading:[/bold] {file_id[:16]}...")
    
    async def do_download():
        node = PeerNode(config)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Connecting...", total=100)
            
            async def on_progress(p: DownloadProgress):
                progress.update(
                    task,
                    completed=p.completed_chunks,
                    total=p.total_chunks,
                    description=f"Downloading ({p.completed_chunks}/{p.total_chunks} chunks)"
                )
            
            result = await node.download(
                file_id,
                output_dir=output,
                on_progress=on_progress
            )
        
        if result.success:
            console.print()
            console.print(Panel.fit(
                f"[bold green]Download Complete![/bold green]\n\n"
                f"[bold]Filename:[/bold] {result.filename}\n"
                f"[bold]Saved to:[/bold] {result.output_path}\n"
                f"[bold]Size:[/bold] {result.bytes_downloaded:,} bytes\n"
                f"[bold]Chunks:[/bold] {result.downloaded_chunks}",
                title="Download Complete"
            ))
        else:
            console.print(f"[bold red]Download failed:[/bold red] {result.error}")
            sys.exit(1)
    
    try:
        asyncio.run(do_download())
    except KeyboardInterrupt:
        console.print("\n[yellow]Download cancelled[/yellow]")


@cli.command()
@click.option('--id', '-i', 'file_id', default=None, help='File ID to check (or list all)')
@click.pass_context
def status(ctx, file_id):
    """Check download status.
    
    Shows progress for a specific download or lists all incomplete downloads.
    
    Example:
        python -m cli.main status --id abc123...
        python -m cli.main status  # List all
    """
    config = get_config_from_options(ctx)
    
    async def do_status():
        node = PeerNode(config)
        
        if file_id:
            # Show specific file status
            status_dict = await node.get_status(file_id)
            
            if status_dict:
                table = Table(title=f"Download Status: {file_id[:16]}...")
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="green")
                
                table.add_row("Filename", status_dict.get('filename', 'Unknown'))
                table.add_row("Progress", f"{status_dict.get('percent', 0):.1f}%")
                table.add_row("Chunks", f"{status_dict.get('completed_chunks', 0)}/{status_dict.get('total_chunks', 0)}")
                table.add_row("Downloaded", f"{status_dict.get('bytes_downloaded', 0):,} bytes")
                table.add_row("Speed", f"{status_dict.get('speed_bps', 0)/1024:.1f} KB/s")
                table.add_row("Complete", "Yes" if status_dict.get('is_complete') else "No")
                
                console.print(table)
            else:
                console.print(f"[yellow]No progress found for {file_id[:16]}...[/yellow]")
        else:
            # List all incomplete downloads
            incomplete = await node.list_incomplete()
            
            if incomplete:
                table = Table(title="Incomplete Downloads")
                table.add_column("File ID", style="cyan")
                table.add_column("Filename", style="white")
                table.add_column("Progress", style="green")
                table.add_column("Chunks", style="yellow")
                
                for item in incomplete:
                    table.add_row(
                        item['file_id'][:16] + "...",
                        item.get('filename', 'Unknown'),
                        f"{item.get('percent', 0):.1f}%",
                        f"{item.get('completed_chunks', 0)}/{item.get('total_chunks', 0)}"
                    )
                
                console.print(table)
            else:
                console.print("[green]No incomplete downloads[/green]")
    
    asyncio.run(do_status())


@cli.command()
@click.option('--id', '-i', 'file_id', required=True, help='File ID to seed')
@click.option('--host', default=None, help='Host to advertise')
@click.pass_context
def seed(ctx, file_id, host):
    """Resume seeding a previously uploaded file.
    
    Use this to continue sharing a file after restarting.
    Requires the chunks to still be in the chunks directory.
    
    Example:
        python -m cli.main seed --id abc123...
    """
    config = get_config_from_options(ctx)
    
    console.print(f"[bold]Seeding:[/bold] {file_id[:16]}...")
    
    async def do_seed():
        node = PeerNode(config)
        
        success = await node.seed(
            file_id,
            peer_host=host or config.tracker_host,
            peer_port=config.peer_port
        )
        
        if success:
            console.print(Panel.fit(
                f"[bold green]Now Seeding[/bold green]\n\n"
                f"[bold]File ID:[/bold] {file_id[:32]}...\n"
                f"[bold]Port:[/bold] {config.peer_port}\n\n"
                f"[dim]Press Ctrl+C to stop seeding[/dim]",
                title="Seeding"
            ))
            
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                await node.stop_all()
                console.print("\n[yellow]Stopped seeding[/yellow]")
        else:
            console.print("[bold red]Failed to start seeding[/bold red]")
            console.print("[dim]Make sure chunks exist in the chunks directory[/dim]")
            sys.exit(1)
    
    try:
        asyncio.run(do_seed())
    except KeyboardInterrupt:
        console.print("\n[yellow]Seeding stopped[/yellow]")


@cli.command()
@click.pass_context
def info(ctx):
    """Show configuration information."""
    config = get_config_from_options(ctx)
    config.ensure_directories()
    
    table = Table(title="P2P Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Tracker Host", config.tracker_host)
    table.add_row("Tracker Port", str(config.tracker_port))
    table.add_row("Peer Port", str(config.peer_port))
    table.add_row("Uploads Dir", config.uploads_dir)
    table.add_row("Download Dir", config.download_dir)
    table.add_row("Chunks Dir", config.chunks_dir)
    table.add_row("Progress Dir", config.progress_dir)
    table.add_row("Chunk Size", f"{config.chunk_size / 1024 / 1024:.0f} MB")
    table.add_row("Max Concurrent", str(config.max_concurrent_downloads))
    
    console.print(table)


@cli.command('list')
@click.option('--uploads', '-u', is_flag=True, help='List files in uploads folder')
@click.option('--downloads', '-d', is_flag=True, help='List files in downloads folder')
@click.pass_context
def list_files(ctx, uploads, downloads):
    """List files in uploads or downloads folder.
    
    Examples:
        python -m cli.main list --uploads
        python -m cli.main list --downloads
        python -m cli.main list  (shows both)
    """
    config = get_config_from_options(ctx)
    config.ensure_directories()
    
    show_both = not uploads and not downloads
    
    if uploads or show_both:
        uploads_path = Path(config.uploads_dir)
        files = list(uploads_path.iterdir()) if uploads_path.exists() else []
        files = [f for f in files if f.is_file()]
        
        table = Table(title="📤 Uploads Folder")
        table.add_column("#", style="dim")
        table.add_column("Filename", style="cyan")
        table.add_column("Size", style="green")
        
        if files:
            for i, f in enumerate(files, 1):
                size = f.stat().st_size
                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f} MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} bytes"
                table.add_row(str(i), f.name, size_str)
        else:
            table.add_row("-", "[dim]No files[/dim]", "-")
        
        console.print(table)
        console.print(f"[dim]Folder: {uploads_path.absolute()}[/dim]")
        console.print()
    
    if downloads or show_both:
        downloads_path = Path(config.download_dir)
        files = list(downloads_path.iterdir()) if downloads_path.exists() else []
        files = [f for f in files if f.is_file()]
        
        table = Table(title="📥 Downloads Folder")
        table.add_column("#", style="dim")
        table.add_column("Filename", style="cyan")
        table.add_column("Size", style="green")
        
        if files:
            for i, f in enumerate(files, 1):
                size = f.stat().st_size
                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f} MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} bytes"
                table.add_row(str(i), f.name, size_str)
        else:
            table.add_row("-", "[dim]No files[/dim]", "-")
        
        console.print(table)
        console.print(f"[dim]Folder: {downloads_path.absolute()}[/dim]")


def main():
    """Entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
