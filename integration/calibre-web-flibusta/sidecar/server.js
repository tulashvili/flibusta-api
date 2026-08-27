const express = require('express');
const client = require('./src/flibustaClient');
const { buildRouter } = require('./src/routes');

const app = express();
app.use(buildRouter(client));

const port = Number(process.env.PORT || 8080);
app.listen(port, () => console.log(`flibusta-sidecar listening on ${port}`));
