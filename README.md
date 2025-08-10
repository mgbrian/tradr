# TRADR

## How to Run

### If this is the first time, install dependencies first

1. Make the install and start scripts executable:

   `chmod +x install.sh start.sh`

1. Run the install script

   `./install.sh`

### Setting Up IBKR

1.  Setup IB Gateway with a paper account.
1.  Configure IBKR API settings (Enable socket clients, trusted IPs)

        *TODO: Flesh this out.*
        - https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
        - https://www.interactivebrokers.com/campus/ibkr-quant-news/interactive-brokers-gateway-install-setup/

### Running the app

If all of the above steps have been completed before:

1. Run the app:

   `./start.sh`
