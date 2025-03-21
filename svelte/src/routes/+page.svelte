<script lang="ts">
	import { browser } from "$app/environment";

	let files = $state<FileList>();

	let info_hash = $state("");

	let started = $state(false);

	if (browser) {
		info_hash = localStorage.getItem("info_hash") || "";
	}

	let fileUploader: HTMLInputElement;

	// JSON typing is not trivial. https://github.com/microsoft/TypeScript/issues/1897
	let status = $state.raw<any>();

	const pollStatus = async () => {
		setInterval(async () => {
			if (info_hash === "") {
				return;
			}
			try {
				const response = await fetch(
					`http://localhost:8000/api/status/${info_hash}`,
				);

				if (!response.ok) {
					throw new Error("Get status failed");
				}

				const data = await response.json();

				status = data;
			} catch (error) {
				console.error(
					"error connecting to backend, clearing localStorage for current operations",
				);
				info_hash = "";
				localStorage.setItem("info_hash", info_hash);
			}
		}, 1000);
	};

	pollStatus();

	const getBarPositions = (
		completed: Array<number>,
		piece_count: number,
	): { x: number; width: number }[] => {
		const width = 700 / piece_count;

		return completed.map((piece) => {
			return {
				x: piece * width,
				width: width,
			};
		});
	};

	const handleUpload = async () => {
		if (!files) {
			return;
		}

		const formData = new FormData();
		formData.append("file", files[0]);

		try {
			const response = await fetch("http://localhost:8000/api/upload/", {
				method: "POST",
				body: formData,
			});

			if (!response.ok) {
				throw new Error("Upload failed");
			}

			const data = await response.json();
			info_hash = data.info_hash;
			started = true;
			localStorage.setItem("info_hash", info_hash);
		} catch (error) {
			console.error("Error");
		}
	};

	const startTorrent = async () => {
		try {
			const response = await fetch(
				`http://localhost:8000/api/start/${info_hash}`,
				{
					method: "POST",
				},
			);

			if (!response.ok) {
				throw new Error("Start failed");
			}

			started = true;
		} catch (error) {
			console.error("Error");
		}
	};

	const stopTorrent = async () => {
		try {
			const response = await fetch(
				`http://localhost:8000/api/stop/${info_hash}`,
				{
					method: "POST",
				},
			);

			if (!response.ok) {
				throw new Error("Stop failed");
			}

			started = false;
		} catch (error) {
			console.error("Error");
		}
		started = false;
	};

	const handleButtonClick = async () => {
		if (status) {
			if (
				confirm(
					`Already downloading: ${status.torrent_name}. Adding a new torrent will cancel that download. Are you sure you want to continue?`,
				)
			) {
				await stopTorrent();
			} else {
				return;
			}
		}
		fileUploader.click();
	};
</script>

<div class="container">
	<h1>Welcome to Pytorrent</h1>

	<button type="button" onclick={handleButtonClick}>Upload Torrent</button>
	<input
		bind:this={fileUploader}
		bind:files
		onchange={handleUpload}
		accept="application/x-bittorrent"
		id="torrentfile"
		name="torrentfile"
		type="file"
		hidden
	/>

	<div class="status">
		<h3>Download Status</h3>
		<div class="flex-center">
			<div class="status-bar">
				<svg viewBox="0 0 700 70">
					{#if status}
						{#each getBarPositions(status.pieces, status.piece_count) as piece}
							<rect
								class="piece"
								x={piece.x}
								y="0"
								width={piece.width}
								height="70"
							></rect>
						{/each}
					{/if}
				</svg>
			</div>
			{#if status}
				{#if started}
					<button type="button" onclick={stopTorrent}>Stop</button>
				{:else}
					<button type="button" onclick={startTorrent}>Restart</button
					>
				{/if}
			{/if}
		</div>
		<div class="status-box">
			{#if status}
				<ul>
					<li>
						<strong>Name:</strong>
						{status.torrent_name}
					</li>
					<li>
						<strong>Percent complete:</strong>
						{Math.round(
							((status.size - status.left) / status.size) * 10000,
						) / 100}%
					</li>
					<li>
						<strong>Amount left:</strong>
						{status.left}
					</li>
					<li>
						<strong>Amount downloaded:</strong>
						{status.downloaded}
					</li>
					<li>
						<strong>Amount uploaded:</strong>
						{status.uploaded}
					</li>
				</ul>
			{:else}
				<p>No download in progress, upload a file to begin.</p>
			{/if}
		</div>
	</div>
</div>

<style>
	h1,
	h3 {
		margin: auto;
		padding: 2rem;
	}

	p {
		padding: 2rem;
		line-height: 1.6;
	}

	button {
		background-color: rebeccapurple;
		color: white;
		padding: 0.75rem 1.5rem;
		border-radius: 0.5rem;
		margin: auto;
	}

	.container {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	.status {
		flex-grow: 1;
	}

	.flex-center {
		display: flex;
		flex-direction: column;
		align-items: center;
	}

	.status-bar {
		width: 700px;
		height: 70px;
		border: 1px solid rebeccapurple;
		margin: 1rem;
	}

	svg {
		width: 100%;
		height: 100%;
	}

	.piece {
		fill: rebeccapurple;
	}

	.status-box {
		padding: 1rem;
	}

	ul {
		list-style-type: none;
	}

	li {
		padding: 0.5rem;
	}
</style>
