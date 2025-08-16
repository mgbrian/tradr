# TRADR

## How to Run

### Install dependencies if this is the first time running the app

1. Make the install, build, test, and start scripts executable:

   `chmod +x install.sh build.sh test.sh start.sh`

1. Run the install script

   `./install.sh`

1. Look out for any final instructions output by the installer (e.g. to update env variables) and complete the TODO list before moving onto the next step.
   - One of the items in the TODO list will be to populate the generated `env.py` file. This contains important configuration values that must be populated in-order for the application to work properly. Read the IB section below for how to get the IB-related settings.

1. Ensure `npm`, `protoc`, and the necessary gRPC Web plugins are installed before moving onto the next step.
   - See https://github.com/grpc/grpc-web?tab=readme-ov-file#code-generator-plugins
   - TODO: Add these to install script. Also add note to have Homebrew installed if on macOS.

1. Compile service proto

   `./build.sh`

### Set Up IB to Accept API Connections

1.  Set up TWS or IB Gateway. The process it the same for paper or live accounts:
    - Either TWS or IB Gateway work just fine for this, and the process for API connection/setup for either is similar. The key difference between them is that TWS is a full trading platform while IB Gateway is a lighter-weight application focused on providing API access.

    For either, your goal is to do the following (from `Settings > API`):
    - Allow socket connections.
    - Disable _Read Only_ mode.
    - Get the Socket Port number and add it to the `env.py` file generated in the install step. Note that this number will usually be different depending on whether you're in Live or Paper Trading mode. Update it when you change modes.

    - **TWS**
      - Installation and API setup instructions [here](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api)

    - **IB Gateway**
      - Download page [here](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
      - Installation and API setup [here](https://www.interactivebrokers.com/campus/ibkr-quant-news/interactive-brokers-gateway-install-setup/)

1.  You can use `utils/test_setup.py` to verify connection settings. Edit it with your connection settings and run:

    `source .requirements/bin/activate && python utils/test_setup.py`

### Running the app

Skip to this step if all of the above steps have been completed before:

`./start.sh`
