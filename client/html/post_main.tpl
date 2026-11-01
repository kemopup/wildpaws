<div class='content-wrapper transparent post-view'>
    <aside class='sidebar'>
        <nav class='buttons'>
            <article class='previous-post'>
                <% if (ctx.prevPostId) { %>
                    <% if (ctx.editMode) { %>
                        <a rel='prev' href='<%= ctx.getPostEditUrl(ctx.prevPostId, ctx.parameters) %>'>
                    <% } else { %>
                        <a rel='prev' href='<%= ctx.getPostUrl(ctx.prevPostId, ctx.parameters) %>'>
                    <% } %>
                <% } else { %>
                    <a rel='prev' class='inactive'>
                <% } %>
                    <i class='fa fa-chevron-left'></i>
                    <span class='vim-nav-hint'>&lt; Previous post</span>
                </a>
            </article>
            <article class='next-post'>
                <% if (ctx.nextPostId) { %>
                    <% if (ctx.editMode) { %>
                        <a rel='next' href='<%= ctx.getPostEditUrl(ctx.nextPostId, ctx.parameters) %>'>
                    <% } else { %>
                        <a rel='next' href='<%= ctx.getPostUrl(ctx.nextPostId, ctx.parameters) %>'>
                    <% } %>
                <% } else { %>
                    <a rel='next' class='inactive'>
                <% } %>
                    <i class='fa fa-chevron-right'></i>
                    <span class='vim-nav-hint'>Next post &gt;</span>
                </a>
            </article>
            <% if (ctx.canEditPosts || ctx.canDeletePosts || ctx.canFeaturePosts) { %>
            <article class='edit-post'>
                <% if (ctx.editMode) { %>
                    <a href='<%= ctx.getPostUrl(ctx.post.id, ctx.parameters) %>'>
                        <i class='fa fa-reply'></i>
                        <span class='vim-nav-hint'>Back to view mode</span>
                    </a>
                <% } else { %>
                    <a href='<%= ctx.getPostEditUrl(ctx.post.id, ctx.parameters) %>'>
                    <i class='fa fa-pencil'></i>
                    <span class='vim-nav-hint'>Edit post</span>
                    </a>
                <% } %>
            </article>
            <% } %>
        </nav>

        <div class='sidebar-container'></div>
    </aside>

    <div class='content'>
        <div class='post-container'></div>

        <% if (ctx.canListPools && ctx.canViewPools) { %>
            <div class='pool-navigators-container'></div>
        <% } %>

        <div class='after-mobile-controls'>
            <section class='post-description'>
                <header class='description-heading'>
                    <h2>Description</h2>
                    <% if (ctx.canEditPostDescription) { %>
                        <button type='button' class='edit-description' aria-label='Edit description' title='Edit description' aria-expanded='false' aria-controls='post-description-form'><i class='fa fa-pencil' aria-hidden='true'></i></button>
                    <% } %>
                </header>
                <% if (ctx.canEditPostDescription) { %>
                <form id='post-description-form' hidden>
                    <label for='post-description'>Description (supports Markdown)</label>
                    <textarea id='post-description' name='description' rows='12' placeholder='Describe this post. Use **bold**, *italic*, lists, and [links](https://example.com).'><%- ctx.post.description %></textarea>
                    <div class='description-actions'>
                        <button type='button' class='description-preview-toggle' aria-expanded='false' aria-controls='post-description-preview'>Show preview</button>
                        <button type='submit'>Save description</button>
                        <button type='button' class='description-cancel'>Cancel</button>
                    </div>
                    <div id='post-description-preview' class='post-description-content' hidden></div>
                </form>
                <% } %>
                <div class='description-readonly'>
                <% if (ctx.post.description) { %>
                    <div class='post-description-content'><%= ctx.makeMarkdown(ctx.post.description, true) %></div>
                <% } else { %>
                    <p class='empty-description'>No description yet.</p>
                <% } %>
                </div>
                <div class='messages' role='status'></div>
            </section>

            <% if (ctx.canCreateComments) { %>
                <h2>Add comment</h2>
                <div class='comment-form-container'></div>
            <% } %>

            <% if (ctx.canListComments) { %>
                <div class='comments-container'></div>
            <% } %>
        </div>
    </div>
</div>
