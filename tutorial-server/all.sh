set -ex

function cleanup() {
    telegram-send "Exited Tutorial Server Build"
}

trap cleanup EXIT
for img in base notebook deploy-hook cilogon cyol
do
    echo "BUILDING IMAGE: $img"
    docker build -f $img.docker -t einsteintoolkit/et-$img . |& tee $img.out
    telegram-send "BUILT IMAGE: $img"
done
