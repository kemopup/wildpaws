<div class='pool-list'>
    <% if (ctx.response.results.length) { %>
        <ul>
          <% for (let pool of ctx.response.results) { %>
            <li data-pool-id='<%= pool.id %>'>
                <a class='thumbnail-wrapper' href='<%- ctx.formatClientLink("posts", {query: "pool:" + pool.id + " sort:pool,asc"}) %>' aria-label='View posts in <%- pool.names[0] %>'>
                    <% if (ctx.canViewPosts) { %>
                        <%= ctx.makePoolThumbnails(pool.posts) %>
                    <% } %>
                </a>
                <div class='pool-name'>
                  <%= ctx.makePoolLink(pool.id, false, true, pool) %>
                </div>
            </li>
          <% } %>
          <%= ctx.makeFlexboxAlign() %>
        </ul>
    <% } %>
</div>
