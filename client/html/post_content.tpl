<div class='post-content post-type-<%- ctx.post.type %>'>
    <% if (['image', 'animation'].includes(ctx.post.type)) { %>
        <a href='<%- ctx.post.contentUrl %>' class='glightbox'>
            <img class='resize-listener' alt='' src='<%- ctx.post.contentUrl %>'/>
        </a>
    <% } else if (ctx.post.type === 'video') { %>
        <%= ctx.makeElement(
            'video', {
                class: 'resize-listener',
                controls: true,
                loop: (ctx.post.flags || []).includes('loop'),
                playsinline: true,
                autoplay: ctx.autoplay,
            },
            ctx.makeElement('source', {
                type: ctx.post.mimeType,
                src: ctx.post.contentUrl,
            }),
            'Your browser doesn\'t support HTML5 videos.')
        %>
    <% } else { console.log(new Error('Unknown post type')); } %>

    <div class='post-overlay resize-listener<%= ctx.editMode ? '' : ' read-only' %>'></div>
</div>
