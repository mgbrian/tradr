# TRADR

## How to Run

### Install dependencies if this is the first time running the app

1. Make the install, build, test, and start scripts executable:

   `chmod +x install.sh build.sh test.sh start.sh`

1. Run the install script

   `./install.sh`

1. Look out for any final instructions output by the installer (e.g. to update env variables) and complete the TODO list before moving onto the next step.

1. Ensure `npm`, `protoc`, and the necessary gRPC Web plugins are installed before moving onto the next step.
   - See https://github.com/grpc/grpc-web?tab=readme-ov-file#code-generator-plugins
   - TODO: Add these to install script.

1. Compile service proto

   `./build.sh`

### Set Up IBKR

1.  Set up IB Gateway with a paper account.
1.  Configure IBKR API settings

        *TODO: Flesh this out.*
        - https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
        - https://www.interactivebrokers.com/campus/ibkr-quant-news/interactive-brokers-gateway-install-setup/

### Running the app

Skip to this step if all of the above steps have been completed before:

`./start.sh`
