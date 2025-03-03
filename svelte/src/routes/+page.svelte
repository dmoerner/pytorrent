<script lang="ts">
	let files = $state<FileList>();

	let info_hash = $state("");

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
		if (!files) {
			return;
		}

		const formData = new FormData();
		formData.append("file", files[0]);

		try {
			const response = await fetch(
				"http://localhost:8000/api/upload/",
				{
					method: "POST",
					body: formData,
				},
			);

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
</script>

<h1>Welcome to Pytorrent</h1>

<h3>Torrent Upload</h3>
<label>
	Upload a torrent file:
	<input
		bind:files
		accept="application/x-bittorrent"
		id="torrentfile"
		name="torrentfile"
		type="file"
	/>
</label>
<button onclick={handleUpload} type="button" disabled={!!info_hash}
	>Upload</button
>

<h3>Download Status</h3>
{#if status}
	<ul>
		<li>Name: {status.torrent_name}</li>
		<li>
			Percent complete: {Math.round(
				(status.left / status.file_size) * 10000,
			) / 100}%
		</li>
		<li>Amount left: {status.left}</li>
		<li>Completed pieces: {status.pieces}</li>
		<li>Amount downloaded: {status.downloaded}</li>
		<li>Amount uploaded: {status.uploaded}</li>
	</ul>
{:else}
	<p>No download started, upload a file to begin.</p>
{/if}
