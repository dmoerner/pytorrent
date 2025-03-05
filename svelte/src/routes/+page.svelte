<script lang="ts">
	let files = $state<FileList>();

	let info_hash = $state("");

	let fileUploader: HTMLInputElement;

	// JSON typing is not trivial. https://github.com/microsoft/TypeScript/issues/1897
	let status = $state.raw<any>();

	const pollStatus = async (info_hash: string) => {
		const interval = setInterval(async () => {
			try {
				getStatus(info_hash);
			} catch (error) {
				console.error("error polling for updates");
				clearInterval(interval);
			}
		}, 1000);
	};

	const getBarPositions = (
		completed: Array<number>,
		piece_count: number,
		containerWidth: number,
	): { x: number; barWidth: number }[] => {
		const barWidth = containerWidth / piece_count;

		return completed.map((piece) => {
			return {
				x: ((piece - 1) * barWidth) / containerWidth,
				barWidth: barWidth,
			};
		});
	};

	const getStatus = async (info_hash: string) => {
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
			console.error(error);
			throw error;
		}
	};

	const handleUpload = async () => {
		if (fileUploader.files === null) {
			return;
		}
		files = fileUploader.files;

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
			pollStatus(info_hash);
		} catch (error) {
			console.error("Error");
		}
	};

	const handleButtonClick = () => {
		fileUploader.click();
	};
</script>

<div class="container">
	<h1>Welcome to Pytorrent</h1>

	<button type="button" onclick={handleButtonClick}>Upload Torrent</button>
	<input
		bind:this={fileUploader}
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
				<svg viewBox="0 0 1 1">
					{#if status}
						{#each getBarPositions(status.pieces, status.piece_count, 700) as { x, barWidth }}
							<rect
								class="piece"
								{x}
								y="0"
								width={barWidth}
								height="1"
							></rect>
						{/each}
					{/if}
				</svg>
			</div>
		</div>
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

<style>
	* {
		margin: 0;
		padding: 0;
		box-sizing: border-box;
		font-family: Arial, sans-serif;
		line-height: 1.6;
		background-color: #f4f4f4;
		color: #333;
	}

	h1,
	h3 {
		margin: auto;
		padding: 2rem;
	}

	p {
		padding: 2rem;
	}

	svg {
		width: 100%;
		height: 100%;
	}

	.piece {
		fill: rebeccapurple;
		height: 100%;
	}

	button {
		background-color: rebeccapurple;
		color: white;
		padding: 0.75rem 1.5rem;
		border-radius: 0.5rem;
		margin: auto;
	}

	.flex-center {
		display: flex;
		justify-content: center;
	}

	.container {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	.status {
		flex-grow: 1;
	}

	.status-bar {
		width: 700px;
		height: 50px;
		border: 1px solid rebeccapurple;
	}

	ul {
		list-style-type: none;
	}
</style>
