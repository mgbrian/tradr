let client = null;

const connectionStatusText = document.getElementById("connection-status-text")
const connectButton = document.getElementById("connect-button")
const loadPositionsButton = document.getElementById("load-positions-button")
const loadAccountValuesButton = document.getElementById("load-account-values-button")

const positionsTable = document.getElementById("positions-table")
const accountValuesTable = document.getElementById("account-values-table")

connectButton.addEventListener("click", connectToGRPCEndpoint)
loadPositionsButton.addEventListener("click", loadPositions)
loadAccountValuesButton.addEventListener("click", loadAccountValues)


function renderTable(table, rows, columns) {
  const tbody = table.querySelector("tbody")
  tbody.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    for (const col of columns) {
      const td = document.createElement("td");
      td.textContent = (r[col] ?? "").toString();
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function connectToGRPCEndpoint(event) {
  const baseUrl = document.getElementById("grpc-endpoint-url-input").value.trim();
  client = new window.TradingWebClient(baseUrl, { format: "text" });
  connectionStatusText.textContent = "Connected to " + baseUrl
  alert("Client configured with endpoint: " + baseUrl);
}

async function loadPositions () {
  if (!client) {
    alert("Click 'Connect' first.");
    return;
  }
  try {
    const rows = await client.GetPositions();

    renderTable(
      positionsTable,
      rows,
      ["account","symbol","sec_type","exchange","con_id","position","avg_cost"]
    );
  } catch (e) {
    console.error(e);
    alert("Failed to load positions: " + (e.message || e));
  }
}

async function loadAccountValues() {
  if (!client) {
    alert("Click 'Connect' first.");
    return;
  }
  try {
    const rows = await client.GetAccountValues();
    renderTable(
      accountValuesTable,
      rows,
      ["account","tag","currency","value"]
    );
  } catch (e) {
    console.error(e);
    alert("Failed to load account values: " + (e.message || e));
  }
}
