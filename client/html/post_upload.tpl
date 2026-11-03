<div id='post-upload'>
    <section class='post-import' aria-labelledby='post-import-heading'>
        <h2 id='post-import-heading'>Import a post</h2>
        <p>Paste an e621, E6AI, Rule34, TBIB, Gelbooru, Inkbunny, Fur Affinity, Bluesky, or X post link to prepare an upload. Only identified artist and character tags are imported.</p>
        <label for='post-import-url'>Post link</label>
        <div class='post-import-controls'>
            <input id='post-import-url' type='url' name='import-url' placeholder='https://e621.net/posts/12345' autocomplete='off'/>
            <button type='button' class='import-post'>Import post</button>
        </div>
        <p class='hint'>Review the file, sources, tags, rating, and Markdown description below before uploading.</p>
        <p class='hint'>X/Bluesky authors and Inkbunny/Fur Affinity uploaders are credited in the description. Hashtags and uncategorized keywords are not imported.</p>
        <div class='messages' aria-live='polite'></div>
    </section>

    <form>
        <div class='dropper-container'></div>

        <div class='control-strip'>
            <input type='submit' value='Upload all' class='submit'/>

            <span class='skip-duplicates'>
                <%= ctx.makeCheckbox({
                    text: 'Skip duplicate',
                    name: 'skip-duplicates',
                    checked: false,
                }) %>
            </span>

            <span class='always-upload-similar'>
                <%= ctx.makeCheckbox({
                    text: 'Force upload similar',
                    name: 'always-upload-similar',
                    checked: false,
                }) %>
            </span>

            <span class='pause-remain-on-error'>
                <%= ctx.makeCheckbox({
                    text: 'Pause on error',
                    name: 'pause-remain-on-error',
                    checked: true,
                }) %>
            </span>

            <input type='button' value='Cancel' class='cancel'/>
        </div>

        <div class='messages'></div>

        <ul class='uploadables-container'></ul>
    </form>
</div>
