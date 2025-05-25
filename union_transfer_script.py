Python 3.13.2 (tags/v3.13.2:4f8bb39, Feb  4 2025, 15:23:48) [MSC v.1942 64 bit (AMD64)] on win32
Type "help", "copyright", "credits" or "license()" for more information.
import os
import time
import json
import logging
from typing import Dict, Any, Optional, Union as TypingUnion
from dataclasses import dataclass
import requests
from eth_account import Account
from eth_typing import HexStr
from web3 import Web3
from hexbytes import HexBytes
import asyncio

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger.info("Loaded .env file")
except ImportError:
    logger.info("python-dotenv not installed, using system environment variables")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class TransferAssetsParameters:
    """Parameters for asset transfer"""
    amount: int
    auto_approve: bool = False
    destination_chain_id: str = ""
    receiver: str = ""
    denom_address: str = ""
    source_chain_id: str = "80084"  # Berachain testnet

class UnionClientError(Exception):
    """Custom exception for Union client errors"""
    pass

class UnionClient:
    """Python client for Union cross-chain asset transfers"""
    
    def __init__(self, 
                 chain_id: str = "80084", 
                 rpc_url: str = "https://bartio.rpc.berachain.com",
                 private_key: Optional[str] = None):
        
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        self.private_key = private_key or os.getenv("PRIVATE_KEY")
        
        if not self.private_key:
            raise ValueError("Private key not found. Set PRIVATE_KEY environment variable.")
        
        # Ensure private key has 0x prefix
        if not self.private_key.startswith('0x'):
            self.private_key = f"0x{self.private_key}"
        
        # Initialize account and web3
        self.account = Account.from_key(self.private_key)
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        
        # Union API endpoints
        self.union_api_base = "https://api.testnet-9.union.build"
        self.union_graphql = f"{self.union_api_base}/graphql"
        self.session = requests.Session()
        self.session.timeout = 30
        
        logger.info(f"Initialized Union client for chain {chain_id}")
        logger.info(f"Account address: {self.account.address}")
    
    def get_balance(self, token_address: Optional[str] = None) -> Dict[str, Any]:
        """Get account balance (ETH or ERC-20 token)"""
        try:
            if token_address:
                # ERC-20 token balance
                contract_abi = [
                    {
                        "constant": True,
                        "inputs": [{"name": "_owner", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "balance", "type": "uint256"}],
                        "type": "function"
                    }
                ]
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=contract_abi
                )
                balance = contract.functions.balanceOf(self.account.address).call()
                return {"balance": balance, "token": token_address}
            else:
                # ETH balance
                balance = self.w3.eth.get_balance(self.account.address)
                return {"balance": balance, "token": "ETH"}
                
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise UnionClientError(f"Balance check failed: {e}")
    
    def approve_transaction(self, transfer_params: TransferAssetsParameters) -> Dict[str, Any]:
        """Approve token transfer (for ERC-20 tokens)"""
        try:
            logger.info(f"Approving transfer for {transfer_params.denom_address}")
            
            # ERC-20 approve function ABI
            approve_abi = [
                {
                    "constant": False,
                    "inputs": [
                        {"name": "_spender", "type": "address"},
                        {"name": "_value", "type": "uint256"}
                    ],
                    "name": "approve",
                    "outputs": [{"name": "", "type": "bool"}],
                    "type": "function"
                }
            ]
            
            # Union bridge contract address (you'll need to get this from Union docs)
            union_bridge_address = "0x0000000000000000000000000000000000000000"  # Placeholder
            
            # Create contract instance
            token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(transfer_params.denom_address),
                abi=approve_abi
            )
            
            # Build approval transaction
            approve_txn = token_contract.functions.approve(
                union_bridge_address,
                transfer_params.amount
            ).build_transaction({
                'chainId': int(self.chain_id),
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
            })
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(approve_txn, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            logger.info(f"Approval successful: {receipt.transactionHash.hex()}")
            return {
                "success": True,
                "tx_hash": receipt.transactionHash.hex(),
                "gas_used": receipt.gasUsed
            }
            
        except Exception as e:
            logger.error(f"Approval failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def transfer_asset(self, transfer_params: TransferAssetsParameters) -> Dict[str, Any]:
        """Execute cross-chain asset transfer via Union"""
        try:
            logger.info(f"Initiating transfer: {transfer_params.amount} tokens to {transfer_params.receiver}")
            
            # Prepare transfer payload for Union API
            transfer_payload = {
                "source_chain_id": transfer_params.source_chain_id,
                "destination_chain_id": transfer_params.destination_chain_id,
                "sender": self.account.address,
                "receiver": transfer_params.receiver,
                "token_address": transfer_params.denom_address,
                "amount": str(transfer_params.amount),
                "auto_approve": transfer_params.auto_approve
            }
            
            # Call Union transfer API
            transfer_url = f"{self.union_api_base}/v1/transfer"
            response = self.session.post(transfer_url, json=transfer_payload)
            
            if response.status_code == 200:
                result = response.json()
                
                # If Union returns transaction data to sign
                if "transaction" in result:
                    tx_data = result["transaction"]
                    
                    # Sign the transaction
                    signed_txn = self.w3.eth.account.sign_transaction(tx_data, self.private_key)
                    tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
                    
                    # Wait for confirmation
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
                    
                    logger.info(f"Transfer successful: {receipt.transactionHash.hex()}")
                    return {
                        "success": True,
                        "tx_hash": receipt.transactionHash.hex(),
                        "gas_used": receipt.gasUsed,
                        "block_number": receipt.blockNumber
                    }
                else:
                    logger.info(f"Transfer initiated: {result}")
                    return {"success": True, "result": result}
            else:
                error_msg = f"Transfer API call failed: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
                
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return {"success": False, "error": str(e)}
    
    def query_graphql(self, query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute GraphQL query against Union API"""
        try:
            graphql_url = f"{self.union_api_base}/graphql"
            payload = {
                "query": query,
                "variables": variables or {}
            }
            
            response = self.session.post(
                graphql_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"GraphQL query failed: {response.status_code} - {response.text}"}
                
        except Exception as e:
            logger.error(f"GraphQL query failed: {e}")
            return {"error": str(e)}
    
    def get_user_transfers(self, address: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        """Get latest transfers for a user address"""
        address = address or self.account.address.lower()
        
        query = """
        query GetLatestUserTransfers($addresses: [String!]!, $limit: Int!) @cached(ttl: 1) {
            v2_transfers(args: { 
                p_limit: $limit, 
                p_addresses_canonical: $addresses 
            }) {
                sender_canonical
                receiver_canonical
                base_amount
                base_token_meta {
                    denom
                    representations {
                        name
                        symbol
                        decimals
                    }
                }
                source_universal_chain_id
                destination_universal_chain_id
            }
        }
        """
        
        variables = {
            "addresses": [address],
            "limit": limit
        }
        
        return self.query_graphql(query, variables)
    
    def get_transfer_status(self, tx_hash: str) -> Dict[str, Any]:
        """Check the status of a cross-chain transfer"""
        try:
            # First try REST API
            status_url = f"{self.union_api_base}/v1/transfer/status/{tx_hash}"
            response = self.session.get(status_url)
            
            if response.status_code == 200:
                return response.json()
            
            # Fallback to GraphQL query for transfer details
            query = """
            query GetTransferByHash($hash: String!) {
                v2_transfers(where: { transaction_hash: { _eq: $hash } }) {
                    sender_canonical
                    receiver_canonical
                    base_amount
                    base_token_meta {
                        denom
                        representations {
                            name
                            symbol
                            decimals
                        }
                    }
                    source_universal_chain_id
                    destination_universal_chain_id
                    status
                    created_at
                    updated_at
                }
            }
            """
            
            result = self.query_graphql(query, {"hash": tx_hash})
            if "data" in result and result["data"]["v2_transfers"]:
                return {"transfers": result["data"]["v2_transfers"]}
            else:
                return {"error": f"Transfer not found: {tx_hash}"}
                
        except Exception as e:
            logger.error(f"Status check failed: {e}")
            return {"error": str(e)}
    
    def wait_for_destination_confirmation(self, tx_hash: str, max_attempts: int = 60) -> Dict[str, Any]:
        """Wait for cross-chain transfer to complete on destination chain"""
        logger.info(f"Waiting for cross-chain confirmation: {tx_hash}")
        
        for attempt in range(max_attempts):
            try:
                status = self.get_transfer_status(tx_hash)
                
                if status.get("status") == "completed":
                    logger.info(f"Cross-chain transfer completed: {tx_hash}")
                    return status
                elif status.get("status") == "failed":
                    logger.error(f"Cross-chain transfer failed: {status}")
                    return status
                
                logger.info(f"Transfer status: {status.get('status', 'pending')} (attempt {attempt + 1}/{max_attempts})")
                time.sleep(10)  # Wait 10 seconds between checks
                
            except Exception as e:
                logger.warning(f"Error checking transfer status: {e}")
                time.sleep(10)
        
        return {"error": "Transfer confirmation timeout"}

def create_union_client(chain_id: str = "80084", 
                       rpc_url: str = "https://bartio.rpc.berachain.com") -> UnionClient:
    """Create Union client instance"""
    return UnionClient(chain_id=chain_id, rpc_url=rpc_url)

def automated_cross_chain_transfer():
    """Example of automated cross-chain transfer with GraphQL monitoring"""
    try:
        # Initialize Union client
        client = create_union_client()
        
        # Check existing transfers first
        logger.info("Checking recent transfers...")
        recent_transfers = client.get_user_transfers()
        if "data" in recent_transfers:
            transfers = recent_transfers["data"]["v2_transfers"]
            logger.info(f"Found {len(transfers)} recent transfers")
            for transfer in transfers[:3]:  # Show last 3
                token_symbol = transfer["base_token_meta"]["representations"][0]["symbol"] if transfer["base_token_meta"]["representations"] else "Unknown"
                logger.info(f"  {transfer['base_amount']} {token_symbol}: {transfer['sender_canonical']} → {transfer['receiver_canonical']}")
        
        # Transfer parameters matching your TypeScript example
        transfer_params = TransferAssetsParameters(
            amount=1,  # 1 token (adjust decimals as needed)
            auto_approve=False,  # Manual approval like in your example
            destination_chain_id="stride-internal-1",
            receiver="stride17ttpfu2xsmfxu6shl756mmxyqu33l5ljegnwps",
            denom_address="0x0E4aaF1351de4c0264C5c7056Ef3777b41BD8e03"  # HONEY contract
        )
        
        # Check balance before transfer
        logger.info("Checking token balance...")
        balance = client.get_balance(transfer_params.denom_address)
        logger.info(f"Current HONEY balance: {balance}")
        
        # Step 1: Approve the transfer (manual approval)
        logger.info("Step 1: Approving token transfer...")
        approval = client.approve_transaction(transfer_params)
        
        if not approval["success"]:
            logger.error(f"Approval failed: {approval['error']}")
            return
        
        logger.info(f"Approval hash: {approval['tx_hash']}")
        
        # Step 2: Execute the transfer
        logger.info("Step 2: Executing cross-chain transfer...")
        transfer = client.transfer_asset(transfer_params)
        
        if not transfer["success"]:
            logger.error(f"Transfer failed: {transfer['error']}")
            return
        
        logger.info(f"Transfer hash: {transfer['tx_hash']}")
...         
...         # Step 3: Wait for destination confirmation and monitor via GraphQL
...         if "tx_hash" in transfer:
...             logger.info("Step 3: Waiting for destination confirmation...")
...             confirmation = client.wait_for_destination_confirmation(transfer['tx_hash'])
...             
...             if "error" not in confirmation:
...                 logger.info("Cross-chain transfer completed successfully!")
...                 
...                 # Query updated transfers to confirm
...                 updated_transfers = client.get_user_transfers(limit=5)
...                 if "data" in updated_transfers:
...                     logger.info("Updated transfer history:")
...                     for transfer in updated_transfers["data"]["v2_transfers"][:2]:
...                         token_info = transfer["base_token_meta"]["representations"][0] if transfer["base_token_meta"]["representations"] else {}
...                         token_symbol = token_info.get("symbol", "Unknown")
...                         amount = int(transfer['base_amount']) / (10 ** token_info.get("decimals", 0)) if token_info.get("decimals") else transfer['base_amount']
...                         logger.info(f"  {amount} {token_symbol}: Chain {transfer['source_universal_chain_id']} → Chain {transfer['destination_universal_chain_id']}")
...                 
...                 return confirmation
...             else:
...                 logger.error(f"Transfer confirmation failed: {confirmation['error']}")
...         
...     except Exception as e:
...         logger.error(f"Automated transfer failed: {e}")
...         raise
... 
... if __name__ == "__main__":
...     # Set your private key as environment variable
...     # export PRIVATE_KEY="your_private_key_without_0x_prefix"
...     
...     # Run the automated cross-chain transfer
