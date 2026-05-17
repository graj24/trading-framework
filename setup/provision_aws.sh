#!/usr/bin/env bash
# Provision EC2 instance on AWS for the trading framework.
# Run this from your Mac AFTER `aws configure`.
# Usage: bash setup/provision_aws.sh
set -e

REGION="ap-south-1"
INSTANCE_TYPE="t3.medium"
KEY_NAME="trading-key"
SG_NAME="trading-sg"
APP_NAME="trading-framework"

echo "=== Provisioning Trading Framework on AWS (${REGION}) ==="

# 1. Create key pair (saves .pem locally)
if [ ! -f ~/.ssh/${KEY_NAME}.pem ]; then
  echo "--> Creating key pair: ${KEY_NAME}"
  aws ec2 create-key-pair \
    --region "${REGION}" \
    --key-name "${KEY_NAME}" \
    --query 'KeyMaterial' \
    --output text > ~/.ssh/${KEY_NAME}.pem
  chmod 400 ~/.ssh/${KEY_NAME}.pem
  echo "    Saved to ~/.ssh/${KEY_NAME}.pem"
else
  echo "--> Key pair already exists at ~/.ssh/${KEY_NAME}.pem"
fi

# 2. Create security group
echo "--> Creating security group: ${SG_NAME}"
SG_ID=$(aws ec2 create-security-group \
  --region "${REGION}" \
  --group-name "${SG_NAME}" \
  --description "Trading framework security group" \
  --query 'GroupId' --output text 2>/dev/null || \
  aws ec2 describe-security-groups \
    --region "${REGION}" \
    --group-names "${SG_NAME}" \
    --query 'SecurityGroups[0].GroupId' --output text)
echo "    Security group ID: ${SG_ID}"

# Allow SSH, HTTP
aws ec2 authorize-security-group-ingress --region "${REGION}" --group-id "${SG_ID}" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "${REGION}" --group-id "${SG_ID}" \
  --protocol tcp --port 80 --cidr 0.0.0.0/0 2>/dev/null || true

# 3. Get latest Amazon Linux 2023 AMI
echo "--> Finding latest Amazon Linux 2023 AMI..."
AMI_ID=$(aws ec2 describe-images \
  --region "${REGION}" \
  --owners amazon \
  --filters \
    "Name=name,Values=al2023-ami-2023*-x86_64" \
    "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text)
echo "    AMI: ${AMI_ID}"

# 4. Launch instance
echo "--> Launching EC2 instance (${INSTANCE_TYPE})..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "${REGION}" \
  --image-id "${AMI_ID}" \
  --instance-type "${INSTANCE_TYPE}" \
  --key-name "${KEY_NAME}" \
  --security-group-ids "${SG_ID}" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${APP_NAME}}]" \
  --query 'Instances[0].InstanceId' \
  --output text)
echo "    Instance ID: ${INSTANCE_ID}"

# 5. Wait for instance to be running
echo "--> Waiting for instance to start..."
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"

# 6. Allocate and associate Elastic IP
echo "--> Allocating Elastic IP..."
ALLOC_ID=$(aws ec2 allocate-address \
  --region "${REGION}" \
  --domain vpc \
  --query 'AllocationId' --output text)
ELASTIC_IP=$(aws ec2 describe-addresses \
  --region "${REGION}" \
  --allocation-ids "${ALLOC_ID}" \
  --query 'Addresses[0].PublicIp' --output text)
aws ec2 associate-address \
  --region "${REGION}" \
  --instance-id "${INSTANCE_ID}" \
  --allocation-id "${ALLOC_ID}" > /dev/null
echo "    Elastic IP: ${ELASTIC_IP}"

# 7. Save details
cat > setup/.aws_instance_info << EOF
INSTANCE_ID=${INSTANCE_ID}
ELASTIC_IP=${ELASTIC_IP}
ALLOC_ID=${ALLOC_ID}
SG_ID=${SG_ID}
REGION=${REGION}
KEY_PEM=~/.ssh/${KEY_NAME}.pem
EOF

echo ""
echo "=== EC2 instance ready ==="
echo ""
echo "  Instance ID : ${INSTANCE_ID}"
echo "  Elastic IP  : ${ELASTIC_IP}"
echo "  SSH key     : ~/.ssh/${KEY_NAME}.pem"
echo ""
echo "Wait ~30 seconds for SSH to become available, then:"
echo ""
echo "  1. Copy code to server:"
echo "     bash setup/deploy.sh ${ELASTIC_IP} ~/.ssh/${KEY_NAME}.pem"
echo ""
echo "  2. SSH in and run setup:"
echo "     ssh -i ~/.ssh/${KEY_NAME}.pem ec2-user@${ELASTIC_IP}"
echo "     bash /app/setup/server_setup.sh"
echo ""
echo "  3. Create your .env file on the server:"
echo "     nano /app/.env"
echo ""
echo "  4. Start services:"
echo "     sudo systemctl start trading-api trading-daemon nginx"
echo ""
echo "  App will be at: http://${ELASTIC_IP}"
